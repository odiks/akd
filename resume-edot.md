# Résumé complet — EDOT (Elastic Distributions of OpenTelemetry)

## 1. Qu'est-ce qu'EDOT ?

Écosystème open source d'Elastic basé sur OpenTelemetry (OTel), composé de :

- **EDOT Collector** — distribution Elastic de l'OTel Collector
- **EDOT SDKs** — Java, .NET, Node.js, Python, PHP, Android, iOS (instrumentation applicative)
- **EDOT Cloud Forwarders** — AWS (GA), Azure/GCP (Technical Preview)

Objectif : offrir un chemin OpenTelemetry natif et vendor-neutral, avec le support entreprise Elastic, en remplacement progressif de Beats/APM agents classiques.

---

## 2. Test EDOT sans Fleet Server (mode standalone)

Possible en téléchargeant le binaire (`elastic-agent` / `otelcol`) et en le lançant avec un fichier YAML de config, **en root** (nécessaire pour `filelog` et `hostmetrics`).

Ingestion directe vers Elasticsearch via l'exporter `elasticsearch`, en mode :

- `otel` — préserve la sémantique OTel (défaut)
- `ecs` — compatibilité rétro avec l'écosystème Beats/ECS

```yaml
receivers:
  filelog:
    include:
      - /var/log/syslog
      - /var/log/auth.log
    start_at: beginning

processors:
  resourcedetection:
    detectors: ["system"]
  batch:
    timeout: 1s

exporters:
  elasticsearch/otel:
    endpoints: ["https://your-es-host:9200"]
    api_key: "id:key"
    mapping:
      mode: otel

service:
  pipelines:
    logs:
      receivers: [filelog]
      processors: [resourcedetection, batch]
      exporters: [elasticsearch/otel]
```

---

## 3. EDOT → Logstash : pas de chemin officiel simple

Pas d'exporter Logstash natif dans le Collector OTel générique, et Logstash n'a pas de receiver OTLP protobuf stable.

**Solutions de contournement :**

| Option | Description |
|---|---|
| `otlphttp` (JSON) → Logstash `http` input | Nécessite un filtre Ruby pour déplier `resourceLogs → scopeLogs → logRecords` |
| Kafka comme buffer intermédiaire | Pattern plus robuste en prod (découplage + rejouabilité) |
| **Hybrid Elastic Agent (9.3+)** | Exporter Logstash **natif** — bien plus mature que le bricolage OTLP/JSON |

---

## 4. Pourquoi le parsing OTLP imbriqué existe

La structure `resourceLogs → scopeLogs → logRecords` est le **format sur le fil (wire format)** du protocole OTLP — imposée par le protobuf OpenTelemetry, pas une contrainte Elastic.

L'exporter `elasticsearch` du Collector fait ce déballage **en interne, en Go, avant sérialisation**, et produit directement des documents plats indexables via l'API Bulk. C'est pour cette raison qu'il "élimine" le problème : il le déplace côté Collector plutôt que de le laisser à la charge de Logstash.

---

## 5. Résilience à grande échelle (1000+ hosts)

### Architecture recommandée

```
[1000+ hosts]                [Gateway layer]              [Elasticsearch]
Agent EDOT     ──OTLP──▶    Gateway EDOT       ──bulk──▶   Cluster ES
(filelog local)             (pool, LB, buffer)              (data nodes)
```

### Niveaux de protection

1. **Agents** : sending queue locale + retry/backoff, idéalement avec `file_storage` (write-ahead log disque) pour survivre à un crash
2. **Load balancing** devant les Gateways :
   - LB réseau classique (HAProxy / NLB) avec health checks
   - `loadbalancingexporter` natif OTel (routing par trace ID, utile en Kubernetes)
3. **En cas de panne Elasticsearch** :
   - La sending queue absorbe le choc à court terme
   - ⚠️ Limite dure par défaut : ~5 min de retry avant drop des données les plus anciennes
   - Pour une vraie résilience : **Kafka comme buffer durable** entre Gateway et ES
4. Dimensionner les **coordinating nodes ES** pour encaisser le pic de rattrapage au retour du cluster

```yaml
exporters:
  elasticsearch/otel:
    sending_queue:
      enabled: true
      queue_size: 5000
      num_consumers: 20
      block_on_overflow: true
    retry_on_failure:
      max_elapsed_time: 10m
```

---

## 6. Migrer Beats/Logstash vers EDOT Agent/Gateway : pertinent ?

Pas de réponse tranchée — dépend de la maturité de tes besoins.

**Arguments pour :**
- Direction stratégique d'Elastic (Elastic Agent tourne déjà sur runtime EDOT depuis 9.2/9.3)
- Standard ouvert, vendor-neutral
- Unification logs/metrics/traces dans un seul pipeline

**Arguments contre :**
- Logstash a 10+ ans de maturité (grok, plugins riches, communauté)
- OTTL est plus jeune, moins outillé (moins de plugins tiers)
- Coût de réécriture non négligeable sur un gros parc

**Recommandation :** approche hybride/pilote progressif plutôt qu'un big-bang ; garder Logstash pour le parsing complexe existant.

---

## 7. Fleet peut-il piloter EDOT ?

Oui, mais avec une distinction cruciale, confirmée depuis **Elastic Agent 9.3** :

| Type de collector | Fleet management |
|---|---|
| **Hybrid Elastic Agent** (Fleet-managé) | ✅ Complet — monitoring, policies, Beat receivers, exporter Logstash, Elastic Defend |
| **Hybrid Agent standalone** | ❌ Pas de management central (monitoring "planned") |
| **EDOT Collector standalone** (binaire OTel pur) | ❌ Aucun enrôlement Fleet possible, jamais |

Le "Hybrid Agent" combine collecte Beats (ECS) et collecte OTel-native (OTLP) **dans le même process**, piloté par un seul `elastic-agent.yml`.

> **Sources officielles à vérifier** : `elastic.co/docs/reference/fleet/elastic-agent-as-otel-collector`, blog Observability Labs (mars 2026) — le tableau comparatif complet a été consulté via une preview de PR, à confirmer sur la doc publique finale.

---

## 8. Mixer Beat receiver + processors OTel dans une même pipeline

**Possible et documenté.** Un Beat receiver (`filebeatreceiver`, `metricbeatreceiver`) est exposé comme un receiver OTel standard, branchable à des processors OTel classiques (`transform`/OTTL, `resourcedetection`, `batch`...) dans la même section `service.pipelines`.

```yaml
receivers:
  filebeatreceiver:
    filebeat:
      inputs:
        - type: filestream
          paths: ["/var/log/syslog"]
          data_stream:
            dataset: generic
          index: logs-generic-default

processors:
  transform:
    log_statements:
      - context: log
        statements:
          - set(attributes["environment"], "production")

exporters:
  elasticsearch/otel:
    endpoints: ["https://your-es-host:9200"]
    api_key: "id:key"

service:
  pipelines:
    logs:
      receivers: [filebeatreceiver]
      processors: [transform]
      exporters: [elasticsearch/otel]
```

⚠️ **Piège** : les Beat receivers produisent des données au **format ECS** (champs plats `message`, `host.name`...), pas au schéma OTLP (`body` / `attributes` / `resource`) — la syntaxe OTTL doit être adaptée selon le receiver d'origine.

---

## 9. Cas Winlogbeat

**Pas de `winlogbeatreceiver` confirmé officiellement**, contrairement à `filebeatreceiver` / `metricbeatreceiver` (bien documentés, présents dans le code source d'Elastic Agent).

**Alternative disponible dès aujourd'hui :** le receiver OTel natif **`windowseventlogreceiver`** (OTel Collector Contrib, inclus dans EDOT), qui lit directement l'API Windows Event Log.

```yaml
receivers:
  windowseventlog:
    channel: Security
    start_at: end

  windowseventlog/system:
    channel: System
    start_at: beginning

service:
  pipelines:
    logs:
      receivers: [windowseventlog, windowseventlog/system]
      exporters: [elasticsearch/otel]
```

⚠️ **Attention** : ce receiver produit un schéma **OTel-natif**, différent du schéma ECS de Winlogbeat classique — ce qui casse la compatibilité avec des dashboards/détections SIEM existants basés sur ECS.

---

## Fil conducteur général

EDOT est un écosystème **jeune et en évolution rapide** (nouveautés à chaque version mineure : 9.2 → 9.3 → 9.4 en quelques mois), avec une direction stratégique claire d'Elastic vers l'unification autour d'OpenTelemetry — mais une maturité encore inégale selon les composants :

- ✅ Logs Linux : bien couverts, stable
- ⚠️ Logstash : intégration encore jeune
- ⚠️ Winlogbeat : pas encore de Beat receiver dédié

**Recommandation générale : approche progressive et pilote** plutôt qu'une bascule totale, pour toute infrastructure en production à grande échelle.

# Observabilité et alerting de la stack Elastic – procédure de mise en production

Procédure complète pour reproduire en production, sur **Elasticsearch / Kibana 9.5** et **Grafana 13**, ce qui a
été construit et testé dans le lab :

```text
Elastic Agent (System, Logstash, Elasticsearch, Kibana) ─► Elasticsearch
   │  Custom field labels.inventory_hostname = ${env.REAL_HOSTNAME|host.name}   (section 5)
   ▼
Kibana : règles ES|QL ─► connecteur Index ─► alerts-elklab ─► ingest pipeline (host / pipeline en recovered)
                                                  │                                        (sections 1 à 3)
                                                  └─► Grafana : dashboard « Kibana alerts » (Lucene)  (section 4)
Kibana : dashboard « Stack – Process observability v1 (Linux / Windows) »                  (sections 6 et 7)
```

- **Aucun script** : tout se fait dans **Kibana Dev Tools**, dans les écrans Fleet / Kibana / Grafana, et sur les
  hôtes pour la variable d'environnement. Dans Dev Tools, une requête préfixée `kbn:` part vers l'API Kibana, les
  autres vers Elasticsearch.
- **Ordre** : sections 1 à 7 dans l'ordre, puis vérification (8). Chaque section se termine par son contrôle.
- Une seule règle est détaillée (section 3), en exemple : la méthode s'applique telle quelle à toute autre règle
  ES|QL (section 3.5).

## 0. Prérequis et valeurs à adapter

| Élément | Valeur |
|---|---|
| Versions | Elasticsearch / Kibana **9.5** (règles ES\|QL, `INLINE STATS`, contrôles ES\|QL des dashboards) ; Grafana **13** (datasource Elasticsearch, requêtes Lucene) |
| Kibana | `xpack.encryptedSavedObjects.encryptionKey` défini, **identique sur toutes les instances** (sans elle : ni règle, ni connecteur) |
| Licence | Basic suffit (règles ES\|QL, connecteur Index) |
| Droits | Elasticsearch : `manage_security`, `manage_index_templates`, `manage_ilm`, `manage_pipeline` ; Kibana : *Rules*, *Connectors*, *Fleet*, *Saved objects*, *Dashboards* |
| Collecte déjà en place | Elastic Agent géré par Fleet sur les hôtes ; intégration **Logstash** (métriques par l'API `http://127.0.0.1:9600`) sur les nœuds Logstash : la règle de la section 3 lit `metrics-logstash.pipeline-*`. Le reste de la collecte est décrit en section 6 |

Valeurs à remplacer dans les blocs :

| Valeur | À remplacer par |
|---|---|
| `<MOT_DE_PASSE_GRAFANA>` | Mot de passe de l'utilisateur de lecture Grafana |
| `https://<ES_URL>:9200` | URL d'un nœud (ou du load balancer) Elasticsearch, vue depuis Grafana |
| Tag `prod` des règles | Tag de votre périmètre |
| `srv-prod-…` | Nom d'inventaire réel de chaque hôte (CMDB, Ansible…) |
| `<DATA_STREAM_DE_SORTIE>` | Data stream écrit par le pipeline Logstash choisi pour le test (section 3.6) |

## 1. Journal des alertes `alerts-elklab`

Chaque règle écrit un document à chaque **déclenchement** (`firing`) et à chaque **levée** (`recovered`) dans
l'alias `alerts-elklab`. Grafana lit ce journal.

### 1.1 Champs

Mapping **strict** : tout champ imprévu est refusé (le connecteur échoue au lieu d'écrire n'importe quoi).

| Champ | Contenu |
|---|---|
| `time`, `@timestamp` | Heure de l'événement |
| `status` | `firing` (déclenchement) / `recovered` (levée) |
| `priority`, `severity` | `P1` / `P2` ; `critical` / `warning` (`ok` à la levée) |
| `host`, `pipeline` | Nœud et pipeline concernés. **Vides** dans le document `recovered` écrit par Kibana : remplis par l'ingest pipeline (1.3) |
| `problem` | Message lisible propre à la règle |
| `rule`, `rule_id` | Nom et identifiant de la règle |
| `alert_id` | Identifiant de l'alerte : valeurs du dernier `STATS … BY` de la requête, jointes par « , » (ex. `logstash-2,pool1-syslog-es_out,P1`). Identique à chaque nouvelle occurrence |
| `alert_uuid` | Identifiant unique d'**une** occurrence (généré par Kibana) : relie une levée à **son** déclenchement (durée) |
| `alert_key_fields` | **Noms** des colonnes de `alert_id`, dans le même ordre (ex. `host.name,logstash.pipeline.name,priority`), écrits par l'action de la règle |
| `tags` | `alert_id_unparsed` si l'ingest pipeline n'a pas pu découper `alert_id` |

### 1.2 Politique de cycle de vie (ILM)

Rotation à 30 jours ou 10 Go, suppression après 1 an.

```
PUT _ilm/policy/alerts-elklab
{"policy": {"phases": {"hot": {"actions": {"rollover": {"max_age": "30d", "max_primary_shard_size": "10gb"}}},
                        "delete": {"min_age": "365d", "actions": {"delete": {}}}}}}
```

### 1.3 Ingest pipeline `alerts-elklab-alert-key`

**Problème résolu.** Au retour à la normale, Kibana ne fournit plus les valeurs de la ligne ES|QL
(`context.hits` est vide) : `{{#context.hits}}{{_source.host.name}}{{/context.hits}}` donne une chaîne vide, et le
document `recovered` n'aurait ni hôte ni pipeline. Seul `{{alert.id}}` reste disponible.

**Principe.** Chaque action écrit aussi `alert_key_fields`, les **noms** des colonnes de `alert_id`. Le pipeline
associe noms et valeurs, puis remplit **uniquement** `host` et `pipeline` **s'ils sont vides** :

| `alert_key_fields` | `alert_id` | Résultat |
|---|---|---|
| `host.name,logstash.pipeline.name,priority` | `logstash-2,pool1-syslog-es_out,P1` | `host` = `logstash-2`, `pipeline` = `pool1-syslog-es_out` |
| `logstash.pool,logstash.pipeline.name` | `pool1,pool1-syslog_in` | `host` = `pool1` (à défaut de `host.name`), `pipeline` = `pool1-syslog_in` |
| `host.name,system.filesystem.mount_point` | `logstash-1,/DATA` | `host` = `logstash-1`, `pipeline` inchangé |

- **Générique** : aucune règle n'est citée. Ajouter ou modifier une règle ne demande **aucune** modification du
  pipeline, seulement le bon `alert_key_fields` dans son action (section 3.5).
- Document `firing` : `host` et `pipeline` sont déjà remplis par l'action, rien n'est modifié.
- Nombre de valeurs différent du nombre de noms (une valeur contient une virgule) : rien n'est modifié, tag
  `alert_id_unparsed`. Document sans `alert_key_fields` : inchangé.
- Paramètres : `host_fields` (colonnes qui alimentent `host`, la première présente l'emporte) et
  `pipeline_fields`. Ajouter un nom de colonne ici si une future règle identifie l'hôte autrement.

```
PUT _ingest/pipeline/alerts-elklab-alert-key
{
 "description": "alerts-elklab : alert_id (valeurs du BY) associé à alert_key_fields (noms) ; host / pipeline remplis s'ils sont vides (documents recovered). Générique : aucune règle citée.",
 "processors": [
  {
   "script": {
    "lang": "painless",
    "source": "if (ctx.alert_id == null || ctx.alert_key_fields == null) { return; }\nString[] names = ctx.alert_key_fields.splitOnToken(',');\nString[] values = ctx.alert_id.splitOnToken(',');\nif (names.length != values.length) {\n  if (ctx.tags == null) { ctx.tags = new ArrayList(); }\n  ctx.tags.add('alert_id_unparsed');\n  return;\n}\nMap group = new HashMap();\nfor (int i = 0; i < names.length; i++) { group.put(names[i].trim(), values[i]); }\nif (ctx.host == null || ctx.host == '') {\n  for (String f : params.host_fields) { if (group.containsKey(f)) { ctx.host = group.get(f); break; } }\n}\nif (ctx.pipeline == null || ctx.pipeline == '') {\n  for (String f : params.pipeline_fields) { if (group.containsKey(f)) { ctx.pipeline = group.get(f); break; } }\n}",
    "params": {
     "host_fields": [
      "host.name",
      "logstash.pool"
     ],
     "pipeline_fields": [
      "logstash.pipeline.name"
     ]
    }
   }
  }
 ]
}
```

Test (rien n'est écrit) :

```
POST _ingest/pipeline/alerts-elklab-alert-key/_simulate
{"docs": [
  {"_source": {"rule_id": "ls-pipe-queue-persisted", "status": "recovered", "host": "", "pipeline": "",
               "alert_id": "logstash-2,pool1-syslog-es_out,P1", "alert_key_fields": "host.name,logstash.pipeline.name,priority"}},
  {"_source": {"rule_id": "ls-pipe-queue-persisted", "status": "firing", "host": "logstash-2", "pipeline": "pool1-syslog-es_out",
               "alert_id": "logstash-2,pool1-syslog-es_out,P1", "alert_key_fields": "host.name,logstash.pipeline.name,priority"}},
  {"_source": {"rule_id": "exemple", "status": "recovered", "host": "", "pipeline": "",
               "alert_id": "a,b,c", "alert_key_fields": "host.name,logstash.pipeline.name"}}
]}
```

Attendu : 1er document `host` = `logstash-2`, `pipeline` = `pool1-syslog-es_out` ; 2e inchangé ; 3e inchangé avec
`"tags": ["alert_id_unparsed"]`.

### 1.4 Template, premier index et alias

**Ordre impératif : le pipeline (1.3) avant le template.** Un template dont `index.default_pipeline` désigne un
pipeline absent fait échouer **toutes** les écritures du connecteur.

```
PUT _index_template/alerts-elklab
{"index_patterns": ["alerts-elklab-*"], "priority": 500,
 "_meta": {"description": "Journal des alertes Kibana (une ligne par déclenchement / levée), écrit par le connecteur alerts-elklab-index"},
 "template": {"settings": {"number_of_shards": 1, "number_of_replicas": 1,
                           "index.lifecycle.name": "alerts-elklab", "index.lifecycle.rollover_alias": "alerts-elklab",
                           "index.default_pipeline": "alerts-elklab-alert-key"},
   "mappings": {"dynamic": "strict", "properties": {
     "@timestamp": {"type": "date"}, "time": {"type": "date"},
     "host": {"type": "keyword"}, "pipeline": {"type": "keyword"}, "severity": {"type": "keyword"},
     "priority": {"type": "keyword"}, "status": {"type": "keyword"},
     "problem": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 1024}}},
     "rule": {"type": "keyword"}, "rule_id": {"type": "keyword"}, "alert_id": {"type": "keyword"},
     "alert_uuid": {"type": "keyword"}, "alert_key_fields": {"type": "keyword"}, "tags": {"type": "keyword"}}}}}

PUT alerts-elklab-000001
{"aliases": {"alerts-elklab": {"is_write_index": true}}}
```

### 1.5 Contrôle

```
GET alerts-elklab/_mapping
GET alerts-elklab/_settings/index.default_pipeline,index.lifecycle.*
```

Attendu : `"dynamic": "strict"` et 14 champs ; `index.default_pipeline` = `alerts-elklab-alert-key` ;
`index.lifecycle.name` = `alerts-elklab`.

## 2. Connecteur Kibana (Index)

Toutes les règles écrivent dans le journal par ce connecteur (ID fixe `alerts-elklab-index`, référencé par les
règles).

```
POST kbn:/api/actions/connector/alerts-elklab-index
{"connector_type_id": ".index", "name": "Alert log (alerts-elklab)",
 "config": {"index": "alerts-elklab", "refresh": true, "executionTimeField": "@timestamp"}}
```

Test d'écriture, puis nettoyage :

```
POST kbn:/api/actions/connector/alerts-elklab-index/_execute
{"params": {"documents": [{"rule_id": "TEST", "rule": "Connector test", "alert_id": "test-host,test-pipeline", "alert_uuid": "test",
  "alert_key_fields": "host.name,logstash.pipeline.name", "host": "", "pipeline": "",
  "severity": "ok", "priority": "P1", "status": "recovered", "problem": "Write test", "time": "2026-01-01T00:00:00Z"}]}}

GET alerts-elklab/_search?q=rule_id:TEST

POST alerts-elklab/_delete_by_query?refresh=true
{"query": {"term": {"rule_id": "TEST"}}}
```

Attendu : `_execute` → `"status": "ok"` ; le document trouvé a `host` = `test-host` et `pipeline` =
`test-pipeline` (preuve que l'ingest pipeline s'applique aux écritures du connecteur).

## 3. Règle d'alerte (exemple) : Logstash pipeline – persistent queue at risk

### 3.1 Principes communs à toutes les règles

| Réglage | Valeur | Effet |
|---|---|---|
| Type | Elasticsearch query, **ES\|QL** (`.es-query`, `searchType: esqlQuery`) | Logique entièrement dans la requête |
| `groupBy` | `row` | **Une alerte par ligne** de résultat ; `alert_id` = valeurs du dernier `STATS … BY` |
| `schedule` | `1m` | Exécution chaque minute |
| `alert_delay.active` | N | Alerte créée après N exécutions consécutives en anomalie (écarte les pics ponctuels) |
| Actions | `query matched` → document `firing` ; `recovered` → document `recovered` | Une seule fois par changement d'état (`notify_when: onActionGroupChange`) |
| Priorité `[P1/P2]` | Colonne `priority` calculée par la requête, présente dans le `BY` | Une escalade P2 → P1 lève l'alerte P2 et ouvre une alerte P1 |

### 3.2 Ce que surveille la règle

Files **persistantes** (`queue.type: persisted`) des pipelines Logstash, pire valeur des 2 dernières minutes par
nœud et pipeline :

| Priorité | Condition |
|---|---|
| **P1** | remplissage ≥ 50 % avec plus de 1 000 événements en attente, **ou** ≥ 80 %, **ou** file pleine dans moins de 30 min au rythme actuel |
| **P2** | remplissage ≥ 20 % avec plus de 1 000 événements, **ou** ≥ 50 % |

- Le seuil de 1 000 événements écarte la page en cours d'écriture, comptée dans la taille même quand la file est vide.
- « Pleine dans » : place restante ÷ croissance de la file en octets/s (flow metric `queue_persisted_growth_bytes`).
- Une alerte par `host.name` + `logstash.pipeline.name` + `priority` ; confirmée après **2** exécutions.
- Cause typique : la sortie ne suit plus (Elasticsearch lent ou bloqué, pipeline aval arrêté). Ensuite, les inputs
  se bloquent et des logs peuvent être perdus.

```esql
FROM metrics-logstash.pipeline-*
| WHERE logstash.pipeline.name IS NOT NULL
      AND logstash.pipeline.total.queues.type == "persisted"
      AND @timestamp > NOW() - 2 minutes
| STATS used = MAX(logstash.pipeline.total.queues.current_size.bytes),
        max_bytes = MAX(logstash.pipeline.total.queues.max_size.bytes),
        events = MAX(logstash.pipeline.total.queues.events),
        growth_events = AVG(logstash.pipeline.total.flow.queue_persisted_growth_events.last_1_minute),
        growth_bytes = AVG(logstash.pipeline.total.flow.queue_persisted_growth_bytes.last_1_minute)
    BY host.name, logstash.pipeline.name
| WHERE max_bytes > 0
| EVAL fill_pct = ROUND(100.0 * used / max_bytes, 1),
       minutes_to_full = CASE(events > 1000 AND growth_events > 0 AND growth_bytes > 0, ROUND(TO_DOUBLE(max_bytes - used) / growth_bytes / 60.0, 1), NULL),
       p1 = (fill_pct >= 50 AND events > 1000) OR fill_pct >= 80 OR COALESCE(minutes_to_full < 30, false),
       p2 = (fill_pct >= 20 AND events > 1000) OR fill_pct >= 50,
       priority = CASE(p1, "P1", p2, "P2", NULL)
| WHERE priority IS NOT NULL
| EVAL severity = CASE(priority == "P1", "critical", "warning"),
       reason = CONCAT("persistent queue ", TO_STRING(fill_pct), " % full, ", TO_STRING(events), " events waiting", COALESCE(CONCAT(", full in ", TO_STRING(minutes_to_full), " min"), ""))
| STATS reason = MAX(reason),
        severity = MAX(severity),
        fill_pct = MAX(fill_pct),
        events = MAX(events),
        minutes_to_full = MIN(minutes_to_full)
    BY host.name, logstash.pipeline.name, priority
| KEEP host.name,
       logstash.pipeline.name,
       priority,
       severity,
       fill_pct,
       events,
       minutes_to_full,
       reason
```

### 3.3 Création

Ajuster le tag `prod` au besoin, puis :

```
POST kbn:/api/alerting/rule/ls-pipe-queue-persisted
{
  "name": "[P1/P2] Logstash pipeline – persistent queue at risk",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "pipeline", "prod", "P1", "P2", "queue", "pq"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 2},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM metrics-logstash.pipeline-*\n| WHERE logstash.pipeline.name IS NOT NULL\n      AND logstash.pipeline.total.queues.type == \"persisted\"\n      AND @timestamp > NOW() - 2 minutes\n| STATS used = MAX(logstash.pipeline.total.queues.current_size.bytes),\n        max_bytes = MAX(logstash.pipeline.total.queues.max_size.bytes),\n        events = MAX(logstash.pipeline.total.queues.events),\n        growth_events = AVG(logstash.pipeline.total.flow.queue_persisted_growth_events.last_1_minute),\n        growth_bytes = AVG(logstash.pipeline.total.flow.queue_persisted_growth_bytes.last_1_minute)\n    BY host.name, logstash.pipeline.name\n| WHERE max_bytes > 0\n| EVAL fill_pct = ROUND(100.0 * used / max_bytes, 1),\n       minutes_to_full = CASE(events > 1000 AND growth_events > 0 AND growth_bytes > 0, ROUND(TO_DOUBLE(max_bytes - used) / growth_bytes / 60.0, 1), NULL),\n       p1 = (fill_pct >= 50 AND events > 1000) OR fill_pct >= 80 OR COALESCE(minutes_to_full < 30, false),\n       p2 = (fill_pct >= 20 AND events > 1000) OR fill_pct >= 50,\n       priority = CASE(p1, \"P1\", p2, \"P2\", NULL)\n| WHERE priority IS NOT NULL\n| EVAL severity = CASE(priority == \"P1\", \"critical\", \"warning\"),\n       reason = CONCAT(\"persistent queue \", TO_STRING(fill_pct), \" % full, \", TO_STRING(events), \" events waiting\", COALESCE(CONCAT(\", full in \", TO_STRING(minutes_to_full), \" min\"), \"\"))\n| STATS reason = MAX(reason),\n        severity = MAX(severity),\n        fill_pct = MAX(fill_pct),\n        events = MAX(events),\n        minutes_to_full = MIN(minutes_to_full)\n    BY host.name, logstash.pipeline.name, priority\n| KEEP host.name,\n       logstash.pipeline.name,\n       priority,\n       severity,\n       fill_pct,\n       events,\n       minutes_to_full,\n       reason"}, "timeField": "@timestamp", "timeWindowSize": 5, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-pipe-queue-persisted", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}", "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}", "severity": "{{#context.hits}}{{_source.severity}}{{/context.hits}}", "priority": "{{#context.hits}}{{_source.priority}}{{/context.hits}}", "status": "firing", "problem": "Persistent queue at risk on pipeline {{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}} ({{#context.hits}}{{_source.host.name}}{{/context.hits}}): {{#context.hits}}{{_source.reason}}{{/context.hits}}. The output can no longer keep up (Elasticsearch slow or blocked, downstream pipeline stopped): inputs will block, then logs may be lost.", "time": "{{date}}", "alert_key_fields": "host.name,logstash.pipeline.name,priority"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-pipe-queue-persisted", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "", "pipeline": "", "severity": "ok", "priority": "P1/P2", "status": "recovered", "problem": "Persistent queue back to normal (node,pipeline,priority: {{alert.id}})", "time": "{{date}}", "alert_key_fields": "host.name,logstash.pipeline.name,priority"}]}}
  ]
}
```

Points clés des actions :

- `host`, `pipeline`, `severity`, `priority`, `problem` du document `firing` viennent de la ligne ES|QL
  (`{{#context.hits}}{{_source.<colonne>}}{{/context.hits}}`).
- Document `recovered` : `host` et `pipeline` **vides** (Kibana n'a plus la ligne) → remplis par l'ingest pipeline à
  partir de `alert_id` et `alert_key_fields`.
- `alert_key_fields` = `host.name,logstash.pipeline.name,priority` : **exactement** les colonnes du dernier
  `STATS … BY`, dans le même ordre, dans les **deux** actions.

### 3.4 Contrôle

```
GET kbn:/api/alerting/rule/ls-pipe-queue-persisted?filter_path=enabled,execution_status,alert_delay
```

Attendu : `"enabled": true`, `execution_status.status` = `ok` (ou `active` si une file est déjà en anomalie).

### 3.5 Ajouter une autre règle avec la même méthode

1. Écrire et tester la requête ES|QL dans Discover : une ligne par objet en anomalie, colonnes `priority` /
   `severity` si la priorité est calculée, colonne `reason` pour le message.
2. Reprendre le JSON de 3.3 : changer l'ID dans l'URL, `name`, `tags`, `alert_delay`, `esql`, `timeWindowSize`,
   `rule_id` et `problem` des deux actions.
3. **`alert_key_fields`** = colonnes du dernier `STATS … BY`, dans l'ordre, séparées par des virgules, sans
   espace, dans les **deux** actions. Exemple : `BY host.name, system.filesystem.mount_point` →
   `"alert_key_fields": "host.name,system.filesystem.mount_point"`.
4. Si l'hôte de la nouvelle règle est identifié par une colonne autre que `host.name` / `logstash.pool` (ou le
   pipeline autrement que par `logstash.pipeline.name`), ajouter son nom dans `host_fields` / `pipeline_fields` du
   pipeline (1.3) : c'est la seule raison de le modifier.

### 3.6 Test réel

Sur un pipeline **hors production** configuré avec `queue.type: persisted`, bloquer l'écriture de son data
stream de sortie : Logstash réessaie, la file persistante se remplit.

```
PUT <DATA_STREAM_DE_SORTIE>/_settings
{"index.blocks.write": true}
```

Après quelques minutes (selon le débit) : P2, puis P1 à mesure que la file se remplit. Contrôle :

```
POST _query?format=txt
{"query": "FROM alerts-elklab | WHERE rule_id == \"ls-pipe-queue-persisted\" | KEEP time, status, priority, host, pipeline, problem, alert_uuid | SORT time DESC | LIMIT 20"}
```

Débloquer, puis attendre la levée (file vidée) :

```
PUT <DATA_STREAM_DE_SORTIE>/_settings
{"index.blocks.write": null}
```

Attendu : un document `firing` puis un document `recovered` **avec `host` et `pipeline` remplis**, partageant le
même `alert_uuid`. Supprimer ensuite les documents du test :

```
POST alerts-elklab/_delete_by_query?refresh=true
{"query": {"bool": {"filter": [{"term": {"rule_id": "ls-pipe-queue-persisted"}},
                               {"range": {"time": {"gte": "<début du test>", "lte": "<fin du test>"}}}]}}}
```

## 4. Grafana : dashboard « Kibana alerts » (Lucene)

### 4.1 Utilisateur de lecture

```
PUT _security/role/grafana_reader
{"cluster": ["monitor"],
 "indices": [{"names": ["alerts-elklab*"], "privileges": ["read", "view_index_metadata"]}]}

PUT _security/user/grafana_reader
{"password": "<MOT_DE_PASSE_GRAFANA>", "roles": ["grafana_reader"], "full_name": "Grafana - lecture seule"}
```

`monitor` sert au test de santé de la datasource. Ajouter d'autres index au rôle si Grafana doit lire d'autres données.

### 4.2 Datasource `es-alerts-elklab`

Le dashboard référence la datasource par son **UID** `es-alerts-elklab`. L'UI de Grafana ne permet pas de choisir
l'UID : deux méthodes.

**Méthode A (recommandée) : fichier de provisioning** sur le serveur Grafana, par ex.
`/etc/grafana/provisioning/datasources/alerts-elklab.yaml`, puis redémarrage de Grafana :

```yaml
apiVersion: 1
datasources:
  - name: ES - Journal des alertes (alerts-elklab)
    uid: es-alerts-elklab
    type: elasticsearch
    access: proxy
    url: https://<ES_URL>:9200
    basicAuth: true
    basicAuthUser: grafana_reader
    editable: false
    jsonData:
      index: alerts-elklab
      timeField: time
      tlsAuthWithCACert: true
    secureJsonData:
      basicAuthPassword: $__file{/etc/grafana/secrets/es-password}   # fichier lisible par grafana uniquement
      tlsCACert: $__file{/etc/grafana/secrets/ca.crt}                # CA du cluster Elasticsearch
```

**Méthode B : UI.** *Connections → Data sources → Add data source → Elasticsearch* : URL, *Basic auth*
(`grafana_reader`), *With CA cert* (CA du cluster), *Index name* `alerts-elklab`, *Time field name* `time`.
Noter l'UID attribué (fin de l'URL de la page de la datasource) et, à l'étape 4.3, remplacer dans le JSON toutes
les occurrences de `es-alerts-elklab` par cet UID (rechercher / remplacer dans un éditeur).

*Save & test* : « Elasticsearch data source is healthy ».

### 4.3 Import du dashboard

*Dashboards → New → Import* → coller le JSON de l'**annexe A** → choisir le dossier → *Import*.

| Panel | Type | Contenu |
|---|---|---|
| Total alerts / Critical / Warning | stat | Alertes encore actives (dernier document de chaque `alert_id` + `rule_id` = `firing`, 30 derniers jours) : toutes, P1, P2 |
| Nodes in alert | stat | Nombre de nœuds (ou pools) ayant une alerte active |
| Oldest open alert | stat | Ancienneté de la plus vieille alerte active |
| Active alerts | table | Alertes actives : dernier état de chaque alerte |
| Alert history | table | Tous les documents du journal sur la période |
| New alerts per rule (time fired) | timeseries | Déclenchements par règle, empilés |
| Alert history for the period | table | Une ligne par occurrence (`alert_uuid`) : début (`firing`), fin (`recovered`), durée (calculée par Elasticsearch, ou « maintenant − début » si encore active), règle, hôte, problème |
| Annotations | | Déclenchements P1 (rouge), P2 (orange), levées, sur tous les graphiques |

**Pourquoi Lucene, sans ES|QL.** Toutes les requêtes sont en Lucene (`status:firing`, `_exists_:alert_uuid`…) : le
« dernier état » d'une alerte vient des documents bruts regroupés par Grafana (`groupBy`, premier document trié
par `time` décroissant) ; âges et durées sont calculés par Elasticsearch (`bucket_script` avec `${__to}`) ;
les jointures (début / fin d'une occurrence) par les transformations Grafana. En Lucene, l'index interrogé est
celui de la datasource.

### 4.4 Contrôle

Après le test 3.6 : la tuile *Total alerts* a compté l'alerte pendant le test, *Alert history for the period*
montre une ligne avec début, fin et durée, et la levée porte bien l'hôte et le pipeline.

## 5. Nom d'inventaire des hôtes : `labels.inventory_hostname`

Le nom d'hôte remonté par Elastic Agent (`host.name`) ne se modifie pas dans Fleet (seul réglage : *Host name
format*, nom court ou FQDN). Pour avoir le nom d'inventaire (CMDB, Ansible…) dans **toutes** les données, sans
toucher à `host.name` :

```text
Hôte : REAL_HOSTNAME=srv-prod-01 (environnement du service Elastic Agent)
Policy Fleet : Custom field labels.inventory_hostname = ${env.REAL_HOSTNAME|host.name}
          └─► processeur add_fields sur chaque input ─► toutes les intégrations de la policy
Elasticsearch : mapping keyword dans logs@custom et <intégration>@custom ─► champ filtrable et agrégeable
```

**Ordre** : mapping (5.2), variable (5.3), Custom field (5.4), rollover (5.5), contrôle (5.6). Le mapping en
premier : le champ est indexé correctement dès la première donnée.

### 5.1 Repérer les intégrations des policies et leurs templates `@custom`

*Fleet → Agent policies → (policy)* : noter les intégrations (System, Windows, Logstash, Elasticsearch, Kibana…), puis :

```
GET _index_template/logs-*,metrics-*?filter_path=index_templates.name,index_templates.index_template.composed_of
```

Dans `composed_of`, retenir les **`<intégration>@custom`** (`system@custom`, `windows@custom`,
`logstash@custom`, `elasticsearch@custom`, `kibana@custom`…). Ces composants sont conservés lors des mises à
jour des intégrations.

### 5.2 Déclarer le mapping

| Données | Component template | Pourquoi |
|---|---|---|
| Journaux (`logs-*`) | `logs@custom` | Inclus par tous les templates `logs-*` : une seule déclaration |
| Métriques (`metrics-*`) | **`<intégration>@custom`**, un par intégration | **Pas** `metrics@custom` : il est aussi inclus par le template intégré `metrics-prometheus@template`, où `labels` est de type `passthrough` → mapping refusé |

Indispensable pour les data streams en `"dynamic": false` (Logstash, Elasticsearch, Kibana) : sans mapping, le
champ est dans `_source` mais **ni filtrable ni agrégeable** (invisible pour ES|QL, les règles et les filtres).

`PUT _component_template` **remplace tout le composant** : le lire d'abord et renvoyer son contenu existant avec
le champ ajouté.

```
GET _component_template/logs@custom
GET _component_template/system@custom
```

Composant absent (404) : le créer.

```
PUT _component_template/logs@custom
{
  "template": {"mappings": {"properties": {
    "labels": {"properties": {"inventory_hostname": {"type": "keyword"}}}}}},
  "_meta": {"description": "Champs ajoutés par les Custom fields des policies Fleet"}
}
```

Composant existant : **ajouter** le champ aux propriétés existantes. Exemple, `logstash@custom` qui porte déjà
`logstash.pool` :

```
PUT _component_template/logstash@custom
{
  "template": {"mappings": {"properties": {
    "logstash": {"properties": {"pool": {"type": "keyword"}}},
    "labels":   {"properties": {"inventory_hostname": {"type": "keyword"}}}}}},
  "_meta": {"description": "logstash.pool + labels.inventory_hostname"}
}
```

À répéter pour chaque intégration de métriques des policies (`system@custom`, `windows@custom`,
`elasticsearch@custom`, `kibana@custom`…). Erreur « results in invalid composable template [X] » : le template X
déclare `labels` autrement, ne pas ajouter le champ dans ce composant.

**Cluster distant (sortie Fleet *Remote Elasticsearch* avec *Synchronize integrations*)** : les
`<intégration>@custom` sont recopiés sur le cluster distant, mais **pas `logs@custom`** (composant global) : le
créer aussi sur le cluster distant.

### 5.3 Poser la variable sur chaque hôte

À automatiser sur le parc (Ansible, GPO, procédure d'installation). La variable doit être dans l'environnement du
**service** Elastic Agent, et l'agent **redémarré** : il ne la lit qu'au démarrage.

#### Linux : `/etc/sysconfig/elastic-agent` (recommandé)

L'unité systemd créée par `elastic-agent install` lit déjà ce fichier d'environnement facultatif :

```bash
systemctl cat elastic-agent | grep EnvironmentFile
# EnvironmentFile=-/etc/sysconfig/elastic-agent     (le « - » : fichier facultatif)
```

```bash
echo 'REAL_HOSTNAME=srv-prod-logstash-01' | sudo tee /etc/sysconfig/elastic-agent
sudo systemctl restart elastic-agent
# contrôle : la variable est dans l'environnement du process
sudo tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value elastic-agent)/environ | grep REAL_HOSTNAME
```

Aucun fichier systemd modifié, pas de `daemon-reload`. Si `EnvironmentFile` pointe ailleurs (autre distribution),
utiliser ce chemin.

**Alternative : fichier de surcharge systemd** (même résultat) :

```bash
sudo mkdir -p /etc/systemd/system/elastic-agent.service.d
printf '[Service]\nEnvironment=REAL_HOSTNAME=srv-prod-logstash-01\n' | sudo tee /etc/systemd/system/elastic-agent.service.d/real-hostname.conf
sudo systemctl daemon-reload && sudo systemctl restart elastic-agent
```

**À éviter : modifier `/etc/systemd/system/elastic-agent.service`.** Le fichier est généré par l'installeur : une
réinstallation le réécrit, la variable disparaît sans erreur et le champ retombe sur `host.name`.

#### Windows : environnement du service (registre)

Une variable **système** n'est vue par les services qu'**après un redémarrage de Windows**. Pour une prise en
compte immédiate, la définir **dans l'environnement propre au service** : valeur `Environment` (type
`REG_MULTI_SZ`) de la clé `HKLM\SYSTEM\CurrentControlSet\Services\Elastic Agent`, lue à chaque démarrage du
service. PowerShell administrateur :

```powershell
$name = "SRV-PROD-WIN-01"
# 1. environnement propre au service « Elastic Agent » : pris en compte au redémarrage du service
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Services\Elastic Agent" -Name Environment `
  -PropertyType MultiString -Value @("REAL_HOSTNAME=$name") -Force | Out-Null
# 2. (facultatif) variable système : pour les autres programmes, effective pour les services après reboot
[Environment]::SetEnvironmentVariable("REAL_HOSTNAME", $name, "Machine")
Restart-Service "Elastic Agent"
# contrôle
(Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\Elastic Agent").Environment
```

La valeur `Environment` **remplace** la liste existante : si le service en a déjà une, l'y ajouter
(`@($existant + "REAL_HOSTNAME=$name")`). Par GPO : *Préférences → Paramètres Windows → Registre* (valeur
`Environment`, type `REG_MULTI_SZ`, sur la clé du service), puis redémarrage du service.

**Winlogbeat (si installé en autonome)** : même méthode sur la clé du service `winlogbeat`, et dans
`winlogbeat.yml` :

```yaml
processors:
  - add_fields:
      target: labels
      fields:
        inventory_hostname: ${REAL_HOSTNAME:${COMPUTERNAME}}   # repli : nom Windows
```

Puis `Restart-Service winlogbeat`. Les templates Winlogbeat mappent déjà `labels.*` en `keyword`.

### 5.4 Custom field dans la policy

*Fleet → Agent policies → (policy) → **Settings** → **Custom fields** → Add field* :

| Name | Value |
|---|---|
| `labels.inventory_hostname` | `${env.REAL_HOSTNAME\|host.name}` |

*Save changes*, puis vérifier dans *Fleet → Agents* que la nouvelle révision est appliquée à tous les agents.

- Le repli `|host.name` est **indispensable** : sans lui, un hôte sans variable perd ses inputs, sans erreur visible.
- Le champ s'applique à **toutes les intégrations** de la policy, y compris celles sans option *Processors*
  (Logstash, Elasticsearch, Kibana).
- À faire sur **chaque** policy (Linux, Windows, Logstash…).

### 5.5 Appliquer le mapping aux data streams existants

Le mapping ne vaut que pour les **nouveaux** index : attendre le rollover automatique (ILM) ou le forcer, au
minimum sur les data streams en `"dynamic": false` :

```
GET _data_stream/metrics-logstash.*,logs-logstash.*,metrics-elasticsearch.*,metrics-kibana.*?filter_path=data_streams.name
POST metrics-logstash.pipeline-default/_rollover
POST metrics-logstash.node-default/_rollover
```

Data streams **TSDS** (`index_mode: time_series`) : l'ancien index reçoit encore des documents jusqu'à la fin de
sa plage temporelle (environ 30 min après le rollover). Les data streams System indexaient déjà le champ
(mapping dynamique) : rollover facultatif.

### 5.6 Contrôle

```
POST _query?format=txt
{
  "query": """
    FROM metrics-*,logs-*
    | WHERE @timestamp > NOW() - 15 minutes
    | EVAL champ = COALESCE(labels.inventory_hostname, "(absent)")
    | STATS docs = COUNT(*) BY host.name, champ, data_stream.dataset
    | SORT host.name, data_stream.dataset
  """
}
```

| Constat | Signification |
|---|---|
| Valeur de `REAL_HOSTNAME` | Hôte configuré |
| Valeur de `host.name` | Hôte sans variable (repli), inputs actifs |
| « (absent) » sur un data stream d'intégration | Rollover pas encore fait, ou mapping absent du bon `@custom` (document brut : `GET <data-stream>/_search?size=1&_source=labels`) |
| « (absent) » sur `elastic_agent.*` ou une donnée produite par l'application | Normal : les Custom fields ne s'appliquent qu'aux inputs des intégrations |

## 6. Collecte pour le dashboard de la stack

### 6.1 Intégrations nécessaires

| Données | Intégration (Fleet) | Policies | Panneaux du dashboard |
|---|---|---|---|
| `metrics-system.process` | **System** → *System process metrics* | Linux et Windows | CPU, RAM, Disk IO par processus, tableau |
| `metrics-system.network` | **System** → *System network metrics* | Linux et Windows | Réseau IN / OUT par hôte |
| `metrics-elasticsearch.stack_monitoring.*` | **Elasticsearch**, métriques en mode *Stack Monitoring* | Policy ayant accès aux nœuds ES | CPU et heap JVM par nœud |
| `metrics-kibana.stack_monitoring.*` | **Kibana**, métriques en mode *Stack Monitoring* | Policy ayant accès à Kibana | RSS, heap, event loop |
| `metrics-logstash.pipeline` | **Logstash**, métriques (API `http://127.0.0.1:9600`) | Policies des nœuds Logstash | Événements IN / OUT, tableau des pipelines |

Elasticsearch et Kibana tournant souvent en conteneur (sans métriques System par processus), leurs panneaux
utilisent Stack Monitoring. Si ces données sont dans un **cluster de monitoring séparé**, voir 7.3.

### 6.2 Windows : collecter tous les processus de la stack

Par défaut, l'intégration System ne garde que les **5 processus les plus gourmands en CPU et les 5 en mémoire**
(`include_top_n` 5 / 5). Sur Windows (plus de 150 processus), les agents de la stack sortent régulièrement de ce
top : dans le lab, `winlogbeat.exe` n'était présent que dans 21 collectes sur 130. Le filtre `processes`
s'applique **avant** le top N : en le limitant aux processus de la stack (moins de 10 par hôte), ils sont tous
collectés à chaque passage.

*Fleet → Agent policies → (policy Windows) → System → Collect metrics from System instances → System process
metrics → Processes* : remplacer `.*` par :

```text
elastic-agent.*
elastic-otel-collector.*
elastic-endpoint.*
winlogbeat.*
filebeat.*
metricbeat.*
osquery.*
fleet-server.*
java.*
node.*
grafana.*
```

*Save integration*. **Contrepartie** : les autres processus Windows (Defender, explorer…) ne sont plus collectés.
Pour tout garder, mettre plutôt *Top N by CPU* et *Top N by memory* à `0` (tous les processus, volume plus élevé).

**Linux** : même risque sur un hôte très chargé (top 5 / 5). Ne pas restreindre `processes` sur une policy dont
une règle surveille **tous** les processus ; préférer dans ce cas une instance System supplémentaire dédiée aux
processus de la stack.

### 6.3 Contrôle

```
POST _query?format=txt
{"query": "FROM metrics-system.process-* | WHERE @timestamp > NOW() - 5 minutes | STATS samples = COUNT(*) BY host.os.type, host.name, process.name | SORT host.name, process.name"}
```

Attendu : chaque processus de la stack d'un hôte avec environ 30 échantillons sur 5 min (collecte toutes les 10 s).

## 7. Dashboard Kibana « Stack – Process observability v1 (Linux / Windows) »

### 7.1 Contenu

| Section | Source | Contenu |
|---|---|---|
| Tuiles | toutes | Hôtes, processus de la stack suivis, CPU / RAM max d'un processus, heap JVM max d'Elasticsearch, event loop max de Kibana |
| CPU, RAM par processus | `metrics-system.process` | **Top 10** des couples hôte · processus (classés par moyenne sur la période) : CPU en % de l'hôte (tous cœurs), RAM (RSS) – Linux + Windows |
| Disk IO lecture / écriture | `metrics-system.process` | Top 10, Ko/s – **Linux seulement** (non collecté par processus sous Windows) |
| Réseau IN / OUT | `metrics-system.network` | Mbit/s, **une courbe par hôte** (l'intégration ne mesure pas le trafic par processus), hors loopback / interfaces virtuelles |
| Tableau des processus | `metrics-system.process` | 50 processus les plus consommateurs : hôte, OS, processus, CPU moyen / max, RAM, I/O |
| Elasticsearch | Stack Monitoring | CPU du process et heap JVM (% du max) par nœud |
| Kibana | Stack Monitoring | RSS et heap V8 (Mo) ; *event loop utilization* = CPU du thread Node.js (le compteur `cpuacct` de Kibana est faux sous cgroup v2) |
| Tableau Elasticsearch & Kibana | Stack Monitoring | CPU moyen / max, heap, RSS, event loop delay, dernière mesure, par nœud |
| Logstash | `metrics-logstash.pipeline` | Événements IN / OUT par pipeline (top 10) ; tableau : débits, workers occupés, contre-pression |

Noms de processus regroupés par **composant** : `logstash` (JVM dont le chemin contient « logstash »),
`elasticsearch`, `kibana`, `elastic-agent`, `elastic-agent · otel-collector`, `elastic-agent · osquery`,
`elastic-defend`, `fleet-server`, `winlogbeat`, `filebeat`, `metricbeat`, `grafana`, `grafana · plugins`. Linux :
exécutable complet (`process.name` y est tronqué à 15 caractères) ; Windows : `process.name` sans `.exe`. Valeurs
calculées par PID et par intervalle, puis sommées par composant : un redémarrage ne double rien.

**Filtres** (barre en haut) :

| Filtre | Type | Effet |
|---|---|---|
| **Host** | liste des valeurs de `host.name` (data view) | Tous les panneaux |
| **OS** | contrôle ES\|QL `?os_type` : All / linux / windows | Tous les panneaux (Elasticsearch, Kibana, Logstash : considérés « linux ») |
| **Process** | contrôle ES\|QL `?component` : All / composants ci-dessus | Tous les panneaux, sauf le réseau (niveau hôte) |

### 7.2 Import

Le fichier **`stack-observability-v1.prod.ndjson`** (annexe B, à enregistrer tel quel, 2 lignes) contient le data
view `linux-obs-metrics` et le dashboard `stack-observability-v1` :

*Stack Management → Saved objects → Import* → fichier → *Check for existing objects* : *Automatically overwrite
conflicts* → *Import*. Attendu : « 2 objects imported ».

| Objet | ID | Contenu |
|---|---|---|
| Data view | `linux-obs-metrics` | `metrics-system.*,metrics-elasticsearch.stack_monitoring.*,metrics-kibana.stack_monitoring.*,metrics-logstash.*` (sert au filtre *Host*) |
| Dashboard | `stack-observability-v1` | 21 panneaux Lens en ES\|QL, 3 filtres |

Ouvrir *Analytics → Dashboards → Stack – Process observability v1 (Linux / Windows)*. Période par défaut :
dernière heure, rafraîchissement toutes les minutes.

### 7.3 Données dans un autre cluster (recherche inter-clusters)

Les requêtes interrogent les index **du cluster local**. Si une partie des données est dans un autre cluster
(par ex. Stack Monitoring dans un cluster de monitoring, déclaré comme *remote cluster* `monitoring`), ajouter le
motif distant, **avant l'import**, dans un éditeur de texte :

- dans les requêtes : `metrics-elasticsearch.stack_monitoring.node_stats-*` →
  `metrics-elasticsearch.stack_monitoring.node_stats-*,monitoring:metrics-elasticsearch.stack_monitoring.node_stats-*`
  (même principe pour `metrics-kibana.stack_monitoring.stats-*`, `metrics-system.process-*`,
  `metrics-system.network-*`, `metrics-logstash.pipeline-*`) ;
- dans le titre du data view : ajouter `,monitoring:metrics-elasticsearch.stack_monitoring.*` (etc.).

Les deux formes (local seul, local + distant) ont été testées dans le lab.

### 7.4 Limites connues

- **Disk IO par processus : Linux seulement** (l'intégration System ne le collecte pas sous Windows).
- **Réseau : par hôte**, pas par processus ; le filtre *Process* ne s'y applique pas.
- **Top 10 par moyenne** : un pic court peut ne pas apparaître dans les graphiques ; le tableau donne aussi le
  CPU **max** de chaque processus.
- Un processus absent des données (top N de l'intégration System, section 6.2) est absent du dashboard.

### 7.5 Contrôle

- Tuile *Hosts running the stack* = nombre d'hôtes attendus ; *OS* = windows → seuls les hôtes Windows et leurs
  processus (`elastic-agent`, `elastic-agent · otel-collector`, `winlogbeat`) ;
- *Process* = logstash → seuls les nœuds Logstash ; panneaux Logstash remplis, panneaux Elasticsearch / Kibana vides ;
- tableau *Elasticsearch & Kibana nodes* : une ligne par nœud ES et par instance Kibana, *Last metrics* récent.

## 8. Vérification finale

| # | Contrôle | Attendu |
|---|---|---|
| 1 | `GET alerts-elklab/_settings/index.default_pipeline` | `alerts-elklab-alert-key` |
| 2 | `_simulate` du pipeline (1.3) | `host` / `pipeline` remplis sur le document `recovered` |
| 3 | `_execute` du connecteur (2) | `"status": "ok"`, `host` / `pipeline` remplis |
| 4 | Règle `ls-pipe-queue-persisted` (3.4) | `enabled: true`, statut `ok` |
| 5 | Test réel (3.6) | Document `firing` puis `recovered`, même `alert_uuid`, `host` / `pipeline` remplis |
| 6 | Grafana *Save & test* (4.2) | « Elasticsearch data source is healthy » |
| 7 | Grafana « Kibana alerts » (4.4) | Occurrence du test avec début, fin, durée |
| 8 | Requête 5.6 | Nom d'inventaire sur tous les data streams d'intégration |
| 9 | Requête 6.3 | Processus de la stack présents à chaque collecte, Linux et Windows |
| 10 | Dashboard Kibana (7.5) | Panneaux remplis, filtres Host / OS / Process opérationnels |

## 9. Retour arrière

Dans l'ordre :

```
DELETE kbn:/api/saved_objects/dashboard/stack-observability-v1
DELETE kbn:/api/saved_objects/index-pattern/linux-obs-metrics
DELETE kbn:/api/alerting/rule/ls-pipe-queue-persisted
DELETE kbn:/api/actions/connector/alerts-elklab-index
DELETE _index_template/alerts-elklab
DELETE alerts-elklab-*
DELETE _ingest/pipeline/alerts-elklab-alert-key
DELETE _ilm/policy/alerts-elklab
DELETE _security/user/grafana_reader
DELETE _security/role/grafana_reader
```

- **Grafana** : supprimer le dashboard « Kibana alerts » et la datasource (fichier de provisioning, puis
  redémarrage, ou UI).
- **Policy Windows** (6.2) : remettre `.*` dans *Processes*.
- **Nom d'inventaire** (5) : supprimer le Custom field des policies ; supprimer la variable (Linux : ligne de
  `/etc/sysconfig/elastic-agent` ou `real-hostname.conf` puis `daemon-reload` ; Windows : valeur `Environment` du
  service et variable système) puis redémarrer l'agent. Le mapping peut rester (sans effet) ; sinon, retirer le
  champ des `@custom` (pris en compte au rollover suivant).

---

## Annexe A – JSON du dashboard Grafana « Kibana alerts »

À coller dans *Dashboards → New → Import* (section 4.3). Datasource : UID `es-alerts-elklab` (section 4.2).

```json
{
  "uid": "kibana-alerts",
  "title": "Kibana alerts",
  "description": "Alerts written by the Kibana rules into the alerts-elklab journal.",
  "tags": ["alerting"],
  "timezone": "browser",
  "refresh": "30s",
  "time": {"from": "now-24h", "to": "now"},
  "schemaVersion": 39,
  "editable": false,
  "graphTooltip": 1,
  "fiscalYearStartMonth": 0,
  "annotations": {"list": [{"name": "P1 alerts (fired)", "enable": true, "iconColor": "red", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "status:firing AND priority:P1", "timeField": "time", "textField": "problem", "tagsField": "host", "target": {"refId": "Anno", "query": "status:firing AND priority:P1", "limit": 200}}, {"name": "P2 alerts (fired)", "enable": true, "iconColor": "orange", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "status:firing AND priority:P2", "timeField": "time", "textField": "problem", "tagsField": "host", "target": {"refId": "Anno", "query": "status:firing AND priority:P2", "limit": 200}}, {"name": "Recovered alerts", "enable": false, "iconColor": "green", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "status:recovered", "timeField": "time", "textField": "rule", "tagsField": "host", "target": {"refId": "Anno", "query": "status:recovered", "limit": 200}}]},
  "links": [],
  "templating": {"list": []},
  "panels": [
    {"id": 39, "type": "stat", "title": "Total alerts", "description": "All alerts still firing: latest event of each alert (alert_id + rule_id) in alerts-elklab is a firing (last 30 days).", "gridPos": {"x": 0, "y": 0, "w": 4, "h": 4}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "*", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "10000"}}], "bucketAggs": [], "timeField": "time"}], "options": {"reduceOptions": {"values": false, "calcs": ["count"], "fields": "/^alert_id$/"}, "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"}, "fieldConfig": {"defaults": {"unit": "none", "noValue": "0", "decimals": 0, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 1}]}, "color": {"mode": "thresholds"}}, "overrides": []}, "timeFrom": "30d", "hideTimeOverride": true, "transformations": [{"id": "groupBy", "options": {"fields": {"alert_id": {"aggregations": [], "operation": "groupby"}, "rule_id": {"aggregations": [], "operation": "groupby"}, "status": {"aggregations": ["first"], "operation": "aggregate"}, "priority": {"aggregations": ["first"], "operation": "aggregate"}, "host": {"aggregations": ["first"], "operation": "aggregate"}, "time": {"aggregations": ["first"], "operation": "aggregate"}}}}, {"id": "filterByValue", "options": {"type": "include", "match": "all", "filters": [{"fieldName": "status (first)", "config": {"id": "equal", "options": {"value": "firing"}}}]}}]},
    {"id": 40, "type": "stat", "title": "Critical", "description": "Alerts still firing whose latest event has priority P1 (alerts-elklab, last 30 days).", "gridPos": {"x": 4, "y": 0, "w": 5, "h": 4}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "*", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "10000"}}], "bucketAggs": [], "timeField": "time"}], "options": {"reduceOptions": {"values": false, "calcs": ["count"], "fields": "/^alert_id$/"}, "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"}, "fieldConfig": {"defaults": {"unit": "none", "noValue": "0", "decimals": 0, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "red", "value": 1}]}, "color": {"mode": "thresholds"}}, "overrides": []}, "timeFrom": "30d", "hideTimeOverride": true, "transformations": [{"id": "groupBy", "options": {"fields": {"alert_id": {"aggregations": [], "operation": "groupby"}, "rule_id": {"aggregations": [], "operation": "groupby"}, "status": {"aggregations": ["first"], "operation": "aggregate"}, "priority": {"aggregations": ["first"], "operation": "aggregate"}, "host": {"aggregations": ["first"], "operation": "aggregate"}, "time": {"aggregations": ["first"], "operation": "aggregate"}}}}, {"id": "filterByValue", "options": {"type": "include", "match": "all", "filters": [{"fieldName": "status (first)", "config": {"id": "equal", "options": {"value": "firing"}}}, {"fieldName": "priority (first)", "config": {"id": "equal", "options": {"value": "P1"}}}]}}]},
    {"id": 41, "type": "stat", "title": "Warning", "description": "Alerts still firing whose latest event has priority P2 (alerts-elklab, last 30 days).", "gridPos": {"x": 9, "y": 0, "w": 5, "h": 4}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "*", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "10000"}}], "bucketAggs": [], "timeField": "time"}], "options": {"reduceOptions": {"values": false, "calcs": ["count"], "fields": "/^alert_id$/"}, "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"}, "fieldConfig": {"defaults": {"unit": "none", "noValue": "0", "decimals": 0, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 1}]}, "color": {"mode": "thresholds"}}, "overrides": []}, "timeFrom": "30d", "hideTimeOverride": true, "transformations": [{"id": "groupBy", "options": {"fields": {"alert_id": {"aggregations": [], "operation": "groupby"}, "rule_id": {"aggregations": [], "operation": "groupby"}, "status": {"aggregations": ["first"], "operation": "aggregate"}, "priority": {"aggregations": ["first"], "operation": "aggregate"}, "host": {"aggregations": ["first"], "operation": "aggregate"}, "time": {"aggregations": ["first"], "operation": "aggregate"}}}}, {"id": "filterByValue", "options": {"type": "include", "match": "all", "filters": [{"fieldName": "status (first)", "config": {"id": "equal", "options": {"value": "firing"}}}, {"fieldName": "priority (first)", "config": {"id": "equal", "options": {"value": "P2"}}}]}}]},
    {"id": 42, "type": "stat", "title": "Nodes in alert", "description": "Number of distinct nodes (or pools) with an alert still firing (alerts-elklab).", "gridPos": {"x": 14, "y": 0, "w": 5, "h": 4}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "*", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "10000"}}], "bucketAggs": [], "timeField": "time"}], "options": {"reduceOptions": {"values": false, "calcs": ["distinctCount"], "fields": "/^host \\(first\\)$/"}, "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"}, "fieldConfig": {"defaults": {"unit": "none", "noValue": "0", "decimals": 0, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 1}]}, "color": {"mode": "thresholds"}}, "overrides": []}, "timeFrom": "30d", "hideTimeOverride": true, "transformations": [{"id": "groupBy", "options": {"fields": {"alert_id": {"aggregations": [], "operation": "groupby"}, "rule_id": {"aggregations": [], "operation": "groupby"}, "status": {"aggregations": ["first"], "operation": "aggregate"}, "priority": {"aggregations": ["first"], "operation": "aggregate"}, "host": {"aggregations": ["first"], "operation": "aggregate"}, "time": {"aggregations": ["first"], "operation": "aggregate"}}}}, {"id": "filterByValue", "options": {"type": "include", "match": "all", "filters": [{"fieldName": "status (first)", "config": {"id": "equal", "options": {"value": "firing"}}}]}}]},
    {"id": 43, "type": "stat", "title": "Oldest open alert", "description": "Time of the oldest alert still firing (alerts-elklab), shown as time ago.", "gridPos": {"x": 19, "y": 0, "w": 5, "h": 4}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "*", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "10000"}}], "bucketAggs": [], "timeField": "time"}], "options": {"reduceOptions": {"values": false, "calcs": ["min"], "fields": "/^time \\(first\\)$/"}, "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"}, "fieldConfig": {"defaults": {"unit": "dateTimeFromNow", "noValue": "none", "thresholds": {"mode": "absolute", "steps": [{"color": "blue", "value": null}]}, "color": {"mode": "thresholds"}}, "overrides": []}, "timeFrom": "30d", "hideTimeOverride": true, "transformations": [{"id": "groupBy", "options": {"fields": {"alert_id": {"aggregations": [], "operation": "groupby"}, "rule_id": {"aggregations": [], "operation": "groupby"}, "status": {"aggregations": ["first"], "operation": "aggregate"}, "priority": {"aggregations": ["first"], "operation": "aggregate"}, "host": {"aggregations": ["first"], "operation": "aggregate"}, "time": {"aggregations": ["first"], "operation": "aggregate"}}}}, {"id": "filterByValue", "options": {"type": "include", "match": "all", "filters": [{"fieldName": "status (first)", "config": {"id": "equal", "options": {"value": "firing"}}}]}}]},
    {"id": 22, "type": "table", "title": "Active alerts", "description": "Alerts whose latest document in alerts-elklab (last 30 days) is « firing ». Lucene: raw documents, then group by alert_id + rule_id keeping the most recent values.", "gridPos": {"x": 0, "y": 4, "w": 24, "h": 7}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "*", "timeField": "time", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "10000"}}], "bucketAggs": []}], "transformations": [{"id": "groupBy", "options": {"fields": {"alert_id": {"operation": "groupby", "aggregations": []}, "rule_id": {"operation": "groupby", "aggregations": []}, "priority": {"operation": "aggregate", "aggregations": ["first"]}, "severity": {"operation": "aggregate", "aggregations": ["first"]}, "host": {"operation": "aggregate", "aggregations": ["first"]}, "pipeline": {"operation": "aggregate", "aggregations": ["first"]}, "problem": {"operation": "aggregate", "aggregations": ["first"]}, "time": {"operation": "aggregate", "aggregations": ["first"]}, "rule": {"operation": "aggregate", "aggregations": ["first"]}, "status": {"operation": "aggregate", "aggregations": ["first"]}}}}, {"id": "filterByValue", "options": {"type": "include", "match": "all", "filters": [{"fieldName": "status (first)", "config": {"id": "equal", "options": {"value": "firing"}}}]}}, {"id": "organize", "options": {"excludeByName": {"alert_id": true, "rule_id": true, "status (first)": true}, "indexByName": {"priority (first)": 0, "severity (first)": 1, "host (first)": 2, "pipeline (first)": 3, "problem (first)": 4, "time (first)": 5, "rule (first)": 6}, "renameByName": {"priority (first)": "Priority", "severity (first)": "Severity", "host (first)": "Host", "pipeline (first)": "Pipeline", "problem (first)": "Problem", "time (first)": "Time", "rule (first)": "Rule", "status (first)": "Status"}}}, {"id": "sortBy", "options": {"sort": [{"field": "Time", "desc": true}]}}], "options": {"showHeader": true, "cellHeight": "sm", "footer": {"show": true, "reducer": ["count"], "fields": ["Problem"], "countRows": true}, "sortBy": [{"displayName": "Priority", "desc": false}]}, "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": true, "filterable": true}}, "overrides": [{"matcher": {"id": "byName", "options": "Priority"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}, {"id": "custom.width", "value": 80}, {"id": "mappings", "value": [{"type": "value", "options": {"P1": {"color": "red", "index": 0}, "P2": {"color": "orange", "index": 1}, "P3": {"color": "blue", "index": 2}, "P2/P1": {"color": "text", "index": 3}}}]}]}, {"matcher": {"id": "byName", "options": "Severity"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text", "mode": "basic"}}, {"id": "custom.width", "value": 90}, {"id": "mappings", "value": [{"type": "value", "options": {"critical": {"color": "red", "index": 0}, "warning": {"color": "orange", "index": 1}, "ok": {"color": "green", "index": 2}, "info": {"color": "blue", "index": 3}}}]}]}, {"matcher": {"id": "byName", "options": "Time"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 170}]}, {"matcher": {"id": "byName", "options": "Host"}, "properties": [{"id": "custom.width", "value": 150}]}, {"matcher": {"id": "byName", "options": "Pipeline"}, "properties": [{"id": "custom.width", "value": 190}]}, {"matcher": {"id": "byName", "options": "Rule"}, "properties": [{"id": "custom.width", "value": 300}]}, {"matcher": {"id": "byName", "options": "Problem"}, "properties": [{"id": "mappings", "value": [{"type": "regex", "options": {"pattern": "^(.*?[0-9]) B (.*)$", "result": {"text": "$1 % $2", "index": 0}}}]}]}, {"matcher": {"id": "byName", "options": "Rule"}, "properties": [{"id": "mappings", "value": [{"type": "regex", "options": {"pattern": "^\\[[^\\]]*\\] (.*)$", "result": {"text": "$1", "index": 0}}}]}]}]}, "timeFrom": "30d", "hideTimeOverride": true},
    {"id": 23, "type": "table", "title": "Alert history", "gridPos": {"x": 0, "y": 11, "w": 24, "h": 9}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "*", "timeField": "time", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "1000"}}], "bucketAggs": []}, {"refId": "B", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "_exists_:alert_uuid", "timeField": "time", "metrics": [{"id": "1", "type": "min", "field": "time"}, {"id": "2", "type": "max", "field": "time"}, {"id": "3", "type": "max", "settings": {"script": "doc['status'].size() > 0 && doc['status'].value == 'recovered' ? 1 : 0"}}, {"id": "4", "type": "bucket_script", "pipelineVariables": [{"name": "first", "pipelineAgg": "1"}, {"name": "last", "pipelineAgg": "2"}, {"name": "rec", "pipelineAgg": "3"}], "settings": {"script": "params.rec > 0 ? params.last - params.first : ${__to}L - params.first"}}], "bucketAggs": [{"id": "5", "type": "terms", "field": "alert_uuid", "settings": {"size": "1000", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}]}, {"refId": "C", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "status:firing AND _exists_:alert_uuid", "timeField": "time", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "1000"}}], "bucketAggs": []}], "transformations": [{"id": "groupBy", "filter": {"id": "byRefId", "options": "C"}, "options": {"fields": {"alert_uuid": {"operation": "groupby", "aggregations": []}, "host": {"operation": "aggregate", "aggregations": ["first"]}, "pipeline": {"operation": "aggregate", "aggregations": ["first"]}}}}, {"id": "joinByField", "options": {"byField": "alert_uuid", "mode": "outerTabular"}}, {"id": "organize", "options": {"excludeByName": {"alert_uuid": true, "alert_id": true, "rule_id": true, "host": true, "pipeline": true, "severity": true, "_id": true, "_index": true, "_type": true, "highlight": true, "sort": true, "Min": true, "Max": true, "Max 1": true, "Min time": true, "Max time": true}, "indexByName": {"time": 0, "status": 1, "priority": 2, "Bucket Script": 3, "host (first)": 4, "pipeline (first)": 5, "problem": 6, "rule": 7}, "renameByName": {"time": "Time", "status": "Status", "priority": "Priority", "Bucket Script": "Duration", "host (first)": "Host", "pipeline (first)": "Pipeline", "problem": "Problem", "rule": "Rule"}}}, {"id": "filterFieldsByName", "options": {"include": {"names": ["Time", "Status", "Priority", "Duration", "Host", "Pipeline", "Problem", "Rule"]}}}, {"id": "filterByValue", "options": {"type": "exclude", "match": "all", "filters": [{"fieldName": "Time", "config": {"id": "isNull", "options": {}}}]}}, {"id": "sortBy", "options": {"sort": [{"field": "Time", "desc": true}]}}], "options": {"showHeader": true, "cellHeight": "sm", "footer": {"show": true, "reducer": ["count"], "fields": ["Problem"], "countRows": true}}, "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": true, "filterable": true}}, "overrides": [{"matcher": {"id": "byName", "options": "Status"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text", "mode": "basic"}}, {"id": "custom.width", "value": 95}, {"id": "mappings", "value": [{"type": "value", "options": {"firing": {"color": "orange", "index": 0}, "recovered": {"color": "green", "index": 1}}}]}]}, {"matcher": {"id": "byName", "options": "Priority"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}, {"id": "custom.width", "value": 80}, {"id": "mappings", "value": [{"type": "value", "options": {"P1": {"color": "red", "index": 0}, "P2": {"color": "orange", "index": 1}, "P3": {"color": "blue", "index": 2}, "P2/P1": {"color": "text", "index": 3}}}]}]}, {"matcher": {"id": "byName", "options": "Time"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 170}]}, {"matcher": {"id": "byName", "options": "Host"}, "properties": [{"id": "custom.width", "value": 150}]}, {"matcher": {"id": "byName", "options": "Pipeline"}, "properties": [{"id": "custom.width", "value": 190}]}, {"matcher": {"id": "byName", "options": "Duration"}, "properties": [{"id": "unit", "value": "dtdurationms"}, {"id": "custom.width", "value": 150}]}, {"matcher": {"id": "byName", "options": "Problem"}, "properties": [{"id": "mappings", "value": [{"type": "regex", "options": {"pattern": "^(.*?[0-9]) B (.*)$", "result": {"text": "$1 % $2", "index": 0}}}]}]}, {"matcher": {"id": "byName", "options": "Rule"}, "properties": [{"id": "mappings", "value": [{"type": "regex", "options": {"pattern": "^\\[[^\\]]*\\] (.*)$", "result": {"text": "$1", "index": 0}}}]}]}]}, "description": "Every document of alerts-elklab for the period. Duration per alert (alert_uuid) computed by Elasticsearch: recovered - fired, or now - fired while still open. Host / pipeline taken from the firing document."},
    {"id": 24, "type": "timeseries", "title": "New alerts per rule (time fired)", "gridPos": {"x": 0, "y": 20, "w": 24, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "status:firing", "timeField": "time", "metrics": [{"id": "1", "type": "count"}], "bucketAggs": [{"id": "3", "type": "terms", "field": "rule", "settings": {"size": "10", "order": "desc", "orderBy": "_count", "min_doc_count": "1"}}, {"id": "2", "type": "date_histogram", "field": "time", "settings": {"interval": "auto", "min_doc_count": "0"}}]}], "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": true}, "tooltip": {"mode": "multi", "sort": "desc"}}, "fieldConfig": {"defaults": {"unit": "none", "decimals": 0, "min": 0, "custom": {"drawStyle": "bars", "fillOpacity": 80, "stacking": {"mode": "normal", "group": "A"}, "lineWidth": 1, "showPoints": "never"}}, "overrides": []}, "description": "New alerts per rule: firing documents of the alerts-elklab journal (one per alert raised), stacked by rule."},
    {"id": 25, "type": "table", "title": "Alert history for the period", "gridPos": {"x": 0, "y": 28, "w": 24, "h": 9}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "_exists_:alert_uuid", "timeField": "time", "metrics": [{"id": "1", "type": "min", "field": "time"}, {"id": "2", "type": "max", "field": "time"}, {"id": "3", "type": "max", "settings": {"script": "doc['status'].size() > 0 && doc['status'].value == 'recovered' ? 1 : 0"}}, {"id": "4", "type": "bucket_script", "pipelineVariables": [{"name": "first", "pipelineAgg": "1"}, {"name": "last", "pipelineAgg": "2"}, {"name": "rec", "pipelineAgg": "3"}], "settings": {"script": "params.rec > 0 ? params.last - params.first : ${__to}L - params.first"}}, {"id": "5", "type": "bucket_script", "pipelineVariables": [{"name": "last", "pipelineAgg": "2"}, {"name": "rec", "pipelineAgg": "3"}], "settings": {"script": "params.rec > 0 ? params.last : null"}}], "bucketAggs": [{"id": "6", "type": "terms", "field": "alert_uuid", "settings": {"size": "1000", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}]}, {"refId": "B", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "status:firing AND _exists_:alert_uuid", "timeField": "time", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "1000"}}], "bucketAggs": []}], "transformations": [{"id": "renameByRegex", "options": {"regex": "^params\\.rec > 0 \\? params\\.last - .*", "renamePattern": "Duration"}}, {"id": "renameByRegex", "options": {"regex": "^params\\.rec > 0 \\? params\\.last : null$", "renamePattern": "End"}}, {"id": "groupBy", "filter": {"id": "byRefId", "options": "B"}, "options": {"fields": {"alert_uuid": {"operation": "groupby", "aggregations": []}, "priority": {"operation": "aggregate", "aggregations": ["first"]}, "rule": {"operation": "aggregate", "aggregations": ["first"]}, "host": {"operation": "aggregate", "aggregations": ["first"]}, "pipeline": {"operation": "aggregate", "aggregations": ["first"]}, "problem": {"operation": "aggregate", "aggregations": ["first"]}}}}, {"id": "joinByField", "options": {"byField": "alert_uuid", "mode": "outerTabular"}}, {"id": "organize", "options": {"excludeByName": {"alert_uuid": true, "Max time": true}, "indexByName": {"Min": 0, "End": 1, "Duration": 2, "Max ": 3, "priority (first)": 4, "rule (first)": 5, "host (first)": 6, "pipeline (first)": 7, "problem (first)": 8}, "renameByName": {"Min": "Start", "Max ": "Status", "priority (first)": "Priority", "rule (first)": "Rule", "host (first)": "Host", "pipeline (first)": "Pipeline", "problem (first)": "Problem"}}}, {"id": "filterByValue", "options": {"type": "exclude", "match": "all", "filters": [{"fieldName": "Start", "config": {"id": "isNull", "options": {}}}]}}, {"id": "sortBy", "options": {"sort": [{"field": "Start", "desc": true}]}}], "options": {"showHeader": true, "cellHeight": "sm", "footer": {"show": true, "reducer": ["count"], "fields": ["Problem"], "countRows": true}}, "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": true, "filterable": true}}, "overrides": [{"matcher": {"id": "byName", "options": "Start"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 195}]}, {"matcher": {"id": "byName", "options": "End"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 195}, {"id": "noValue", "value": "—"}]}, {"matcher": {"id": "byName", "options": "Duration"}, "properties": [{"id": "unit", "value": "dtdurationms"}, {"id": "custom.width", "value": 150}]}, {"matcher": {"id": "byName", "options": "Status"}, "properties": [{"id": "custom.width", "value": 100}, {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "mappings", "value": [{"type": "value", "options": {"0": {"text": "active", "color": "red", "index": 0}, "1": {"text": "recovered", "color": "green", "index": 1}}}]}]}, {"matcher": {"id": "byName", "options": "Priority"}, "properties": [{"id": "custom.width", "value": 80}, {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}, {"id": "mappings", "value": [{"type": "value", "options": {"P1": {"color": "red", "index": 0}, "P2": {"color": "orange", "index": 1}, "P3": {"color": "blue", "index": 2}, "P2/P1": {"color": "text", "index": 3}}}]}]}, {"matcher": {"id": "byName", "options": "Rule"}, "properties": [{"id": "custom.width", "value": 300}, {"id": "mappings", "value": [{"type": "regex", "options": {"pattern": "^\\[[^\\]]*\\] (.*)$", "result": {"text": "$1", "index": 0}}}]}]}, {"matcher": {"id": "byName", "options": "Host"}, "properties": [{"id": "custom.width", "value": 150}]}, {"matcher": {"id": "byName", "options": "Pipeline"}, "properties": [{"id": "custom.width", "value": 190}]}, {"matcher": {"id": "byName", "options": "Problem"}, "properties": [{"id": "mappings", "value": [{"type": "regex", "options": {"pattern": "^(.*?[0-9]) B (.*)$", "result": {"text": "$1 % $2", "index": 0}}}]}]}]}, "description": "One row per alert (alert_uuid) of the alerts-elklab journal for the period: start (firing), end (recovered), duration (end - start, or now - start while active), computed by Elasticsearch; rule, host and problem from the firing document."}
  ]
}
```

## Annexe B – `stack-observability-v1.prod.ndjson` (dashboard Kibana)

À enregistrer **tel quel** dans un fichier `stack-observability-v1.prod.ndjson` (2 lignes, encodage UTF-8), puis
importer (section 7.2).

```json
{"attributes": {"allowHidden": false, "fieldAttrs": "{}", "fieldFormatMap": "{}", "fields": "[]", "name": "Stack observability – system + stack monitoring", "runtimeFieldMap": "{}", "sourceFilters": "[]", "timeFieldName": "@timestamp", "title": "metrics-system.*,metrics-elasticsearch.stack_monitoring.*,metrics-kibana.stack_monitoring.*,metrics-logstash.*"}, "coreMigrationVersion": "8.8.0", "created_at": "2026-10-31T17:05:41.777Z", "id": "linux-obs-metrics", "managed": false, "references": [], "type": "index-pattern", "typeMigrationVersion": "8.0.0", "updated_at": "2026-10-31T17:05:41.777Z", "version": "WzcyNSw0XQ=="}
{"attributes": {"controlGroupInput": {"chainingSystem": "HIERARCHICAL", "controlStyle": "oneLine", "ignoreParentSettingsJSON": "{\"ignoreFilters\": false, \"ignoreQuery\": false, \"ignoreTimerange\": false, \"ignoreValidations\": false}", "panelsJSON": "{\"host\": {\"type\": \"optionsListControl\", \"order\": 0, \"grow\": true, \"width\": \"medium\", \"explicitInput\": {\"id\": \"host\", \"dataViewId\": \"linux-obs-metrics\", \"fieldName\": \"host.name\", \"title\": \"Host\", \"selectedOptions\": [], \"searchTechnique\": \"prefix\", \"sort\": {\"by\": \"_key\", \"direction\": \"asc\"}}}, \"os\": {\"type\": \"esqlControl\", \"order\": 1, \"grow\": true, \"width\": \"medium\", \"explicitInput\": {\"id\": \"os\", \"title\": \"OS\", \"variableName\": \"os_type\", \"variableType\": \"values\", \"controlType\": \"STATIC_VALUES\", \"esqlQuery\": \"\", \"availableOptions\": [\"All\", \"linux\", \"windows\"], \"selectedOptions\": [\"All\"], \"singleSelect\": true}}, \"component\": {\"type\": \"esqlControl\", \"order\": 2, \"grow\": true, \"width\": \"medium\", \"explicitInput\": {\"id\": \"component\", \"title\": \"Process\", \"variableName\": \"component\", \"variableType\": \"values\", \"controlType\": \"STATIC_VALUES\", \"esqlQuery\": \"\", \"availableOptions\": [\"All\", \"logstash\", \"elasticsearch\", \"kibana\", \"elastic-agent\", \"elastic-agent \\u00b7 otel-collector\", \"elastic-agent \\u00b7 osquery\", \"elastic-defend\", \"fleet-server\", \"winlogbeat\", \"filebeat\", \"metricbeat\", \"grafana\", \"grafana \\u00b7 plugins\"], \"selectedOptions\": [\"All\"], \"singleSelect\": true}}}", "showApplySelections": false}, "description": "First version of the stack process dashboard: resource usage of the Elastic stack processes (Logstash, Elasticsearch, Kibana, Elastic Agent, Winlogbeat, Fleet Server, Grafana) on Linux and Windows hosts. CPU, RAM, disk IO (Linux) per process (top 10 by average), network IN/OUT per host, top processes table, Elasticsearch / Kibana Stack Monitoring, Logstash events per pipeline. Filters: Host, OS, Process.", "kibanaSavedObjectMeta": {"searchSourceJSON": "{\"query\": {\"query\": \"\", \"language\": \"kuery\"}, \"filter\": []}"}, "optionsJSON": "{\"useMargins\": true, \"syncColors\": true, \"syncCursor\": true, \"syncTooltips\": true, \"hidePanelTitles\": false}", "panelsJSON": "[{\"type\": \"lens\", \"panelIndex\": \"hosts\", \"gridData\": {\"x\": 0, \"y\": 0, \"w\": 8, \"h\": 6, \"i\": \"hosts\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Hosts running the stack (Linux / Windows)\", \"type\": \"lens\", \"visualizationType\": \"lnsMetric\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-hosts\", \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS `Hosts` = COUNT_DISTINCT(host.name)\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"Hosts\", \"fieldName\": \"Hosts\", \"meta\": {\"type\": \"number\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-hosts\", \"title\": \"metrics-system.process-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"layerId\": \"layer1\", \"layerType\": \"data\", \"metricAccessor\": \"Hosts\"}, \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS `Hosts` = COUNT_DISTINCT(host.name)\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-hosts\": {\"id\": \"linux-obs-esql-hosts\", \"title\": \"metrics-system.process-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-system.process-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"procs\", \"gridData\": {\"x\": 8, \"y\": 0, \"w\": 8, \"h\": 6, \"i\": \"procs\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Stack processes (host \\u00b7 process)\", \"type\": \"lens\", \"visualizationType\": \"lnsMetric\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-procs\", \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", component)\\n| STATS `Processes` = COUNT_DISTINCT(proc)\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"Processes\", \"fieldName\": \"Processes\", \"meta\": {\"type\": \"number\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-procs\", \"title\": \"metrics-system.process-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"layerId\": \"layer1\", \"layerType\": \"data\", \"metricAccessor\": \"Processes\"}, \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", component)\\n| STATS `Processes` = COUNT_DISTINCT(proc)\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-procs\": {\"id\": \"linux-obs-esql-procs\", \"title\": \"metrics-system.process-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-system.process-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"cpu_top\", \"gridData\": {\"x\": 16, \"y\": 0, \"w\": 8, \"h\": 6, \"i\": \"cpu_top\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Highest stack process CPU (% of the host)\", \"type\": \"lens\", \"visualizationType\": \"lnsMetric\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-cpu_top\", \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS v = AVG(system.process.cpu.total.norm.pct)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, component, process.pid\\n| STATS v = SUM(v)\\n    BY t, host.name, component\\n| STATS `CPU %` = ROUND(MAX(v) * 100, 1)\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"CPU %\", \"fieldName\": \"CPU %\", \"meta\": {\"type\": \"number\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-cpu_top\", \"title\": \"metrics-system.process-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"layerId\": \"layer1\", \"layerType\": \"data\", \"metricAccessor\": \"CPU %\"}, \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS v = AVG(system.process.cpu.total.norm.pct)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, component, process.pid\\n| STATS v = SUM(v)\\n    BY t, host.name, component\\n| STATS `CPU %` = ROUND(MAX(v) * 100, 1)\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-cpu_top\": {\"id\": \"linux-obs-esql-cpu_top\", \"title\": \"metrics-system.process-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-system.process-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"mem_top\", \"gridData\": {\"x\": 24, \"y\": 0, \"w\": 8, \"h\": 6, \"i\": \"mem_top\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Highest stack process RAM (MB, RSS)\", \"type\": \"lens\", \"visualizationType\": \"lnsMetric\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-mem_top\", \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS v = AVG(system.process.memory.rss.bytes)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, component, process.pid\\n| STATS v = SUM(v)\\n    BY t, host.name, component\\n| STATS `RAM MB` = ROUND(MAX(v) / 1048576, 1)\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"RAM MB\", \"fieldName\": \"RAM MB\", \"meta\": {\"type\": \"number\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-mem_top\", \"title\": \"metrics-system.process-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"layerId\": \"layer1\", \"layerType\": \"data\", \"metricAccessor\": \"RAM MB\"}, \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS v = AVG(system.process.memory.rss.bytes)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, component, process.pid\\n| STATS v = SUM(v)\\n    BY t, host.name, component\\n| STATS `RAM MB` = ROUND(MAX(v) / 1048576, 1)\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-mem_top\": {\"id\": \"linux-obs-esql-mem_top\", \"title\": \"metrics-system.process-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-system.process-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"es_heap_top\", \"gridData\": {\"x\": 32, \"y\": 0, \"w\": 8, \"h\": 6, \"i\": \"es_heap_top\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Elasticsearch \\u2013 highest JVM heap (%)\", \"type\": \"lens\", \"visualizationType\": \"lnsMetric\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-es_heap_top\", \"query\": {\"esql\": \"FROM metrics-elasticsearch.stack_monitoring.node_stats-*\\n| WHERE elasticsearch.node.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"elasticsearch\\\")\\n| STATS v = AVG(elasticsearch.node.stats.jvm.mem.heap.used.pct)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), elasticsearch.node.name\\n| STATS `Heap %` = ROUND(MAX(v), 1)\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"Heap %\", \"fieldName\": \"Heap %\", \"meta\": {\"type\": \"number\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-es_heap_top\", \"title\": \"metrics-elasticsearch.stack_monitoring.node_stats-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"layerId\": \"layer1\", \"layerType\": \"data\", \"metricAccessor\": \"Heap %\"}, \"query\": {\"esql\": \"FROM metrics-elasticsearch.stack_monitoring.node_stats-*\\n| WHERE elasticsearch.node.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"elasticsearch\\\")\\n| STATS v = AVG(elasticsearch.node.stats.jvm.mem.heap.used.pct)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), elasticsearch.node.name\\n| STATS `Heap %` = ROUND(MAX(v), 1)\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-es_heap_top\": {\"id\": \"linux-obs-esql-es_heap_top\", \"title\": \"metrics-elasticsearch.stack_monitoring.node_stats-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-elasticsearch.stack_monitoring.node_stats-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"kb_elu_top\", \"gridData\": {\"x\": 40, \"y\": 0, \"w\": 8, \"h\": 6, \"i\": \"kb_elu_top\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Kibana \\u2013 highest event loop utilization (%)\", \"type\": \"lens\", \"visualizationType\": \"lnsMetric\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-kb_elu_top\", \"query\": {\"esql\": \"FROM metrics-kibana.stack_monitoring.stats-*\\n| WHERE kibana.stats.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"kibana\\\")\\n| STATS v = AVG(kibana.stats.process.event_loop_utilization.utilization)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), kibana.stats.name\\n| STATS `ELU %` = ROUND(MAX(v) * 100, 1)\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"ELU %\", \"fieldName\": \"ELU %\", \"meta\": {\"type\": \"number\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-kb_elu_top\", \"title\": \"metrics-kibana.stack_monitoring.stats-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"layerId\": \"layer1\", \"layerType\": \"data\", \"metricAccessor\": \"ELU %\"}, \"query\": {\"esql\": \"FROM metrics-kibana.stack_monitoring.stats-*\\n| WHERE kibana.stats.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"kibana\\\")\\n| STATS v = AVG(kibana.stats.process.event_loop_utilization.utilization)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), kibana.stats.name\\n| STATS `ELU %` = ROUND(MAX(v) * 100, 1)\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-kb_elu_top\": {\"id\": \"linux-obs-esql-kb_elu_top\", \"title\": \"metrics-kibana.stack_monitoring.stats-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-kibana.stack_monitoring.stats-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"cpu\", \"gridData\": {\"x\": 0, \"y\": 6, \"w\": 24, \"h\": 14, \"i\": \"cpu\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"CPU per stack process \\u2013 top 10 (% of the host, all cores) \\u2013 Linux + Windows\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-cpu\", \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS v = AVG(system.process.cpu.total.norm.pct)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, component, process.pid\\n| STATS v = SUM(v)\\n    BY t, host.name, component\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", component),\\n       `CPU %` = ROUND(v * 100, 2)\\n| INLINE STATS avg_proc = AVG(`CPU %`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `CPU %`, proc\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"CPU %\", \"fieldName\": \"CPU %\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"proc\", \"fieldName\": \"proc\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-cpu\", \"title\": \"metrics-system.process-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"bottom\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"CPU %\"], \"splitAccessor\": \"proc\"}]}, \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS v = AVG(system.process.cpu.total.norm.pct)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, component, process.pid\\n| STATS v = SUM(v)\\n    BY t, host.name, component\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", component),\\n       `CPU %` = ROUND(v * 100, 2)\\n| INLINE STATS avg_proc = AVG(`CPU %`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `CPU %`, proc\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-cpu\": {\"id\": \"linux-obs-esql-cpu\", \"title\": \"metrics-system.process-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-system.process-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"mem\", \"gridData\": {\"x\": 24, \"y\": 6, \"w\": 24, \"h\": 14, \"i\": \"mem\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"RAM per stack process \\u2013 top 10 (MB, RSS) \\u2013 Linux + Windows\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-mem\", \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS v = AVG(system.process.memory.rss.bytes)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, component, process.pid\\n| STATS v = SUM(v)\\n    BY t, host.name, component\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", component),\\n       `RAM MB` = ROUND(v / 1048576, 2)\\n| INLINE STATS avg_proc = AVG(`RAM MB`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `RAM MB`, proc\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"RAM MB\", \"fieldName\": \"RAM MB\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"proc\", \"fieldName\": \"proc\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-mem\", \"title\": \"metrics-system.process-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"bottom\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"RAM MB\"], \"splitAccessor\": \"proc\"}]}, \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS v = AVG(system.process.memory.rss.bytes)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, component, process.pid\\n| STATS v = SUM(v)\\n    BY t, host.name, component\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", component),\\n       `RAM MB` = ROUND(v / 1048576, 2)\\n| INLINE STATS avg_proc = AVG(`RAM MB`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `RAM MB`, proc\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-mem\": {\"id\": \"linux-obs-esql-mem\", \"title\": \"metrics-system.process-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-system.process-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"io_read\", \"gridData\": {\"x\": 0, \"y\": 20, \"w\": 24, \"h\": 14, \"i\": \"io_read\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Disk IO read per stack process \\u2013 top 10 (KB/s) \\u2013 Linux only\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-io_read\", \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type) AND host.os.type == \\\"linux\\\"\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| EVAL c = TO_LONG(system.process.io.read_bytes)\\n| STATS c0 = MIN(c), c1 = MAX(c), t0 = MIN(@timestamp), t1 = MAX(@timestamp)\\n    BY t = BUCKET(@timestamp, 30, ?_tstart, ?_tend), host.name, component, process.pid\\n| EVAL span_ms = DATE_DIFF(\\\"millisecond\\\", t0, t1)\\n| WHERE span_ms > 0\\n| EVAL bps = (c1 - c0) * 1000.0 / span_ms\\n| STATS bps = SUM(bps)\\n    BY t, host.name, component\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", component),\\n       `Read KB/s` = ROUND(bps / 1024, 2)\\n| INLINE STATS avg_proc = AVG(`Read KB/s`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `Read KB/s`, proc\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"Read KB/s\", \"fieldName\": \"Read KB/s\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"proc\", \"fieldName\": \"proc\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-io_read\", \"title\": \"metrics-system.process-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"bottom\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"Read KB/s\"], \"splitAccessor\": \"proc\"}]}, \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type) AND host.os.type == \\\"linux\\\"\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| EVAL c = TO_LONG(system.process.io.read_bytes)\\n| STATS c0 = MIN(c), c1 = MAX(c), t0 = MIN(@timestamp), t1 = MAX(@timestamp)\\n    BY t = BUCKET(@timestamp, 30, ?_tstart, ?_tend), host.name, component, process.pid\\n| EVAL span_ms = DATE_DIFF(\\\"millisecond\\\", t0, t1)\\n| WHERE span_ms > 0\\n| EVAL bps = (c1 - c0) * 1000.0 / span_ms\\n| STATS bps = SUM(bps)\\n    BY t, host.name, component\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", component),\\n       `Read KB/s` = ROUND(bps / 1024, 2)\\n| INLINE STATS avg_proc = AVG(`Read KB/s`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `Read KB/s`, proc\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-io_read\": {\"id\": \"linux-obs-esql-io_read\", \"title\": \"metrics-system.process-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-system.process-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"io_write\", \"gridData\": {\"x\": 24, \"y\": 20, \"w\": 24, \"h\": 14, \"i\": \"io_write\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Disk IO write per stack process \\u2013 top 10 (KB/s) \\u2013 Linux only\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-io_write\", \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type) AND host.os.type == \\\"linux\\\"\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| EVAL c = TO_LONG(system.process.io.write_bytes)\\n| STATS c0 = MIN(c), c1 = MAX(c), t0 = MIN(@timestamp), t1 = MAX(@timestamp)\\n    BY t = BUCKET(@timestamp, 30, ?_tstart, ?_tend), host.name, component, process.pid\\n| EVAL span_ms = DATE_DIFF(\\\"millisecond\\\", t0, t1)\\n| WHERE span_ms > 0\\n| EVAL bps = (c1 - c0) * 1000.0 / span_ms\\n| STATS bps = SUM(bps)\\n    BY t, host.name, component\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", component),\\n       `Write KB/s` = ROUND(bps / 1024, 2)\\n| INLINE STATS avg_proc = AVG(`Write KB/s`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `Write KB/s`, proc\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"Write KB/s\", \"fieldName\": \"Write KB/s\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"proc\", \"fieldName\": \"proc\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-io_write\", \"title\": \"metrics-system.process-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"bottom\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"Write KB/s\"], \"splitAccessor\": \"proc\"}]}, \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type) AND host.os.type == \\\"linux\\\"\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| EVAL c = TO_LONG(system.process.io.write_bytes)\\n| STATS c0 = MIN(c), c1 = MAX(c), t0 = MIN(@timestamp), t1 = MAX(@timestamp)\\n    BY t = BUCKET(@timestamp, 30, ?_tstart, ?_tend), host.name, component, process.pid\\n| EVAL span_ms = DATE_DIFF(\\\"millisecond\\\", t0, t1)\\n| WHERE span_ms > 0\\n| EVAL bps = (c1 - c0) * 1000.0 / span_ms\\n| STATS bps = SUM(bps)\\n    BY t, host.name, component\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", component),\\n       `Write KB/s` = ROUND(bps / 1024, 2)\\n| INLINE STATS avg_proc = AVG(`Write KB/s`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `Write KB/s`, proc\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-io_write\": {\"id\": \"linux-obs-esql-io_write\", \"title\": \"metrics-system.process-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-system.process-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"net_in\", \"gridData\": {\"x\": 0, \"y\": 34, \"w\": 24, \"h\": 12, \"i\": \"net_in\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Network IN \\u2013 per host (Mbit/s; per-process traffic not collected, Process filter n/a)\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-net_in\", \"query\": {\"esql\": \"TS metrics-system.network-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type) AND NOT system.network.name RLIKE \\\"lo|veth.*|docker.*|br-.*|virbr.*|cni.*|flannel.*|cali.*|Loopback.*|isatap.*|Teredo.*\\\"\\n| STATS bps = SUM(RATE(`system.network.in.bytes`))\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name\\n| EVAL `IN Mbit/s` = ROUND(bps * 8 / 1000000, 3)\\n| KEEP t, `IN Mbit/s`, host.name\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"IN Mbit/s\", \"fieldName\": \"IN Mbit/s\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"host.name\", \"fieldName\": \"host.name\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-net_in\", \"title\": \"metrics-system.network-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"right\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"IN Mbit/s\"], \"splitAccessor\": \"host.name\"}]}, \"query\": {\"esql\": \"TS metrics-system.network-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type) AND NOT system.network.name RLIKE \\\"lo|veth.*|docker.*|br-.*|virbr.*|cni.*|flannel.*|cali.*|Loopback.*|isatap.*|Teredo.*\\\"\\n| STATS bps = SUM(RATE(`system.network.in.bytes`))\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name\\n| EVAL `IN Mbit/s` = ROUND(bps * 8 / 1000000, 3)\\n| KEEP t, `IN Mbit/s`, host.name\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-net_in\": {\"id\": \"linux-obs-esql-net_in\", \"title\": \"metrics-system.network-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-system.network-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"net_out\", \"gridData\": {\"x\": 24, \"y\": 34, \"w\": 24, \"h\": 12, \"i\": \"net_out\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Network OUT \\u2013 per host (Mbit/s; per-process traffic not collected, Process filter n/a)\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-net_out\", \"query\": {\"esql\": \"TS metrics-system.network-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type) AND NOT system.network.name RLIKE \\\"lo|veth.*|docker.*|br-.*|virbr.*|cni.*|flannel.*|cali.*|Loopback.*|isatap.*|Teredo.*\\\"\\n| STATS bps = SUM(RATE(`system.network.out.bytes`))\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name\\n| EVAL `OUT Mbit/s` = ROUND(bps * 8 / 1000000, 3)\\n| KEEP t, `OUT Mbit/s`, host.name\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"OUT Mbit/s\", \"fieldName\": \"OUT Mbit/s\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"host.name\", \"fieldName\": \"host.name\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-net_out\", \"title\": \"metrics-system.network-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"right\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"OUT Mbit/s\"], \"splitAccessor\": \"host.name\"}]}, \"query\": {\"esql\": \"TS metrics-system.network-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type) AND NOT system.network.name RLIKE \\\"lo|veth.*|docker.*|br-.*|virbr.*|cni.*|flannel.*|cali.*|Loopback.*|isatap.*|Teredo.*\\\"\\n| STATS bps = SUM(RATE(`system.network.out.bytes`))\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name\\n| EVAL `OUT Mbit/s` = ROUND(bps * 8 / 1000000, 3)\\n| KEEP t, `OUT Mbit/s`, host.name\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-net_out\": {\"id\": \"linux-obs-esql-net_out\", \"title\": \"metrics-system.network-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-system.network-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"top\", \"gridData\": {\"x\": 0, \"y\": 46, \"w\": 48, \"h\": 15, \"i\": \"top\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Stack processes over the period (sorted by CPU) \\u2013 Disk IO: Linux only\", \"type\": \"lens\", \"visualizationType\": \"lnsDatatable\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-top\", \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| EVAL r = TO_LONG(system.process.io.read_bytes),\\n       w = TO_LONG(system.process.io.write_bytes)\\n| STATS cpu = AVG(system.process.cpu.total.norm.pct),\\n        rss = AVG(system.process.memory.rss.bytes),\\n        r0 = MIN(r), r1 = MAX(r), w0 = MIN(w), w1 = MAX(w),\\n        t0 = MIN(@timestamp), t1 = MAX(@timestamp)\\n    BY t = BUCKET(@timestamp, 30, ?_tstart, ?_tend), host.name, host.os.type, component, process.pid\\n| EVAL span_ms = DATE_DIFF(\\\"millisecond\\\", t0, t1),\\n       rd = CASE(span_ms > 0, (r1 - r0) * 1000.0 / span_ms, NULL),\\n       wr = CASE(span_ms > 0, (w1 - w0) * 1000.0 / span_ms, NULL)\\n| STATS cpu = SUM(cpu), rss = SUM(rss), rd = SUM(rd), wr = SUM(wr), last_seen = MAX(t1)\\n    BY t, host.name, host.os.type, component\\n| STATS cpu_avg = AVG(cpu), cpu_p95 = PERCENTILE(cpu, 95), cpu_max = MAX(cpu),\\n        rss_avg = AVG(rss), rss_max = MAX(rss), rd = AVG(rd), wr = AVG(wr), last_seen = MAX(last_seen)\\n    BY host.name, host.os.type, component\\n| EVAL `CPU avg %` = ROUND(cpu_avg * 100, 2),\\n       `CPU p95 %` = ROUND(cpu_p95 * 100, 2),\\n       `CPU max %` = ROUND(cpu_max * 100, 2),\\n       `RAM avg MB` = ROUND(rss_avg / 1048576, 0),\\n       `RAM max MB` = ROUND(rss_max / 1048576, 0),\\n       `Read KB/s` = ROUND(rd / 1024, 2),\\n       `Write KB/s` = ROUND(wr / 1024, 2),\\n       `Last seen` = DATE_FORMAT(\\\"yyyy-MM-dd HH:mm:ss\\\", last_seen)\\n| RENAME host.name AS Host, host.os.type AS OS, component AS Process\\n| SORT `CPU avg %` DESC NULLS LAST\\n| LIMIT 50\\n| KEEP Host, OS, Process, `CPU avg %`, `CPU max %`, `RAM avg MB`, `Read KB/s`, `Write KB/s`\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"Host\", \"fieldName\": \"Host\", \"meta\": {\"type\": \"string\"}}, {\"columnId\": \"OS\", \"fieldName\": \"OS\", \"meta\": {\"type\": \"string\"}}, {\"columnId\": \"Process\", \"fieldName\": \"Process\", \"meta\": {\"type\": \"string\"}}, {\"columnId\": \"CPU avg %\", \"fieldName\": \"CPU avg %\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"CPU max %\", \"fieldName\": \"CPU max %\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"RAM avg MB\", \"fieldName\": \"RAM avg MB\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"Read KB/s\", \"fieldName\": \"Read KB/s\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"Write KB/s\", \"fieldName\": \"Write KB/s\", \"meta\": {\"type\": \"number\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-top\", \"title\": \"metrics-system.process-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"layerId\": \"layer1\", \"layerType\": \"data\", \"columns\": [{\"columnId\": \"Host\"}, {\"columnId\": \"OS\"}, {\"columnId\": \"Process\"}, {\"columnId\": \"CPU avg %\"}, {\"columnId\": \"CPU max %\"}, {\"columnId\": \"RAM avg MB\"}, {\"columnId\": \"Read KB/s\"}, {\"columnId\": \"Write KB/s\"}]}, \"query\": {\"esql\": \"FROM metrics-system.process-*\\n| WHERE (?os_type == \\\"All\\\" OR host.os.type == ?os_type)\\n| EVAL exe = COALESCE(MV_LAST(SPLIT(process.executable, \\\"/\\\")), process.name),\\n       exe = CASE(ENDS_WITH(exe, \\\".exe\\\"), SUBSTRING(exe, 1, LENGTH(exe) - 4), exe),\\n       component = CASE(exe == \\\"java\\\" AND process.executable LIKE \\\"*logstash*\\\", \\\"logstash\\\",\\n                        exe == \\\"java\\\" AND process.executable LIKE \\\"*elasticsearch*\\\", \\\"elasticsearch\\\",\\n                        exe == \\\"node\\\" AND process.executable LIKE \\\"*kibana*\\\", \\\"kibana\\\",\\n                        exe == \\\"elastic-agent\\\", \\\"elastic-agent\\\",\\n                        exe LIKE \\\"elastic-otel-co*\\\", \\\"elastic-agent \\u00b7 otel-collector\\\",\\n                        exe LIKE \\\"osquery*\\\", \\\"elastic-agent \\u00b7 osquery\\\",\\n                        exe == \\\"elastic-endpoint\\\", \\\"elastic-defend\\\",\\n                        exe == \\\"fleet-server\\\", \\\"fleet-server\\\",\\n                        exe == \\\"winlogbeat\\\", \\\"winlogbeat\\\",\\n                        exe == \\\"filebeat\\\", \\\"filebeat\\\",\\n                        exe == \\\"metricbeat\\\", \\\"metricbeat\\\",\\n                        exe == \\\"grafana\\\", \\\"grafana\\\",\\n                        exe LIKE \\\"gpx_grafana*\\\", \\\"grafana \\u00b7 plugins\\\",\\n                        NULL)\\n| WHERE component IS NOT NULL AND (?component == \\\"All\\\" OR component == ?component)\\n| EVAL r = TO_LONG(system.process.io.read_bytes),\\n       w = TO_LONG(system.process.io.write_bytes)\\n| STATS cpu = AVG(system.process.cpu.total.norm.pct),\\n        rss = AVG(system.process.memory.rss.bytes),\\n        r0 = MIN(r), r1 = MAX(r), w0 = MIN(w), w1 = MAX(w),\\n        t0 = MIN(@timestamp), t1 = MAX(@timestamp)\\n    BY t = BUCKET(@timestamp, 30, ?_tstart, ?_tend), host.name, host.os.type, component, process.pid\\n| EVAL span_ms = DATE_DIFF(\\\"millisecond\\\", t0, t1),\\n       rd = CASE(span_ms > 0, (r1 - r0) * 1000.0 / span_ms, NULL),\\n       wr = CASE(span_ms > 0, (w1 - w0) * 1000.0 / span_ms, NULL)\\n| STATS cpu = SUM(cpu), rss = SUM(rss), rd = SUM(rd), wr = SUM(wr), last_seen = MAX(t1)\\n    BY t, host.name, host.os.type, component\\n| STATS cpu_avg = AVG(cpu), cpu_p95 = PERCENTILE(cpu, 95), cpu_max = MAX(cpu),\\n        rss_avg = AVG(rss), rss_max = MAX(rss), rd = AVG(rd), wr = AVG(wr), last_seen = MAX(last_seen)\\n    BY host.name, host.os.type, component\\n| EVAL `CPU avg %` = ROUND(cpu_avg * 100, 2),\\n       `CPU p95 %` = ROUND(cpu_p95 * 100, 2),\\n       `CPU max %` = ROUND(cpu_max * 100, 2),\\n       `RAM avg MB` = ROUND(rss_avg / 1048576, 0),\\n       `RAM max MB` = ROUND(rss_max / 1048576, 0),\\n       `Read KB/s` = ROUND(rd / 1024, 2),\\n       `Write KB/s` = ROUND(wr / 1024, 2),\\n       `Last seen` = DATE_FORMAT(\\\"yyyy-MM-dd HH:mm:ss\\\", last_seen)\\n| RENAME host.name AS Host, host.os.type AS OS, component AS Process\\n| SORT `CPU avg %` DESC NULLS LAST\\n| LIMIT 50\\n| KEEP Host, OS, Process, `CPU avg %`, `CPU max %`, `RAM avg MB`, `Read KB/s`, `Write KB/s`\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-top\": {\"id\": \"linux-obs-esql-top\", \"title\": \"metrics-system.process-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-system.process-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"es_cpu\", \"gridData\": {\"x\": 0, \"y\": 61, \"w\": 24, \"h\": 13, \"i\": \"es_cpu\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Elasticsearch \\u2013 process CPU per node (%, Stack Monitoring)\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-es_cpu\", \"query\": {\"esql\": \"FROM metrics-elasticsearch.stack_monitoring.node_stats-*\\n| WHERE elasticsearch.node.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"elasticsearch\\\")\\n| STATS v = AVG(elasticsearch.node.stats.process.cpu.pct)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), elasticsearch.cluster.name, elasticsearch.node.name\\n| EVAL proc = CONCAT(elasticsearch.cluster.name, \\\" \\u00b7 \\\", elasticsearch.node.name),\\n       `CPU %` = ROUND(v, 1)\\n| KEEP t, `CPU %`, proc\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"CPU %\", \"fieldName\": \"CPU %\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"proc\", \"fieldName\": \"proc\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-es_cpu\", \"title\": \"metrics-elasticsearch.stack_monitoring.node_stats-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"bottom\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"CPU %\"], \"splitAccessor\": \"proc\"}]}, \"query\": {\"esql\": \"FROM metrics-elasticsearch.stack_monitoring.node_stats-*\\n| WHERE elasticsearch.node.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"elasticsearch\\\")\\n| STATS v = AVG(elasticsearch.node.stats.process.cpu.pct)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), elasticsearch.cluster.name, elasticsearch.node.name\\n| EVAL proc = CONCAT(elasticsearch.cluster.name, \\\" \\u00b7 \\\", elasticsearch.node.name),\\n       `CPU %` = ROUND(v, 1)\\n| KEEP t, `CPU %`, proc\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-es_cpu\": {\"id\": \"linux-obs-esql-es_cpu\", \"title\": \"metrics-elasticsearch.stack_monitoring.node_stats-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-elasticsearch.stack_monitoring.node_stats-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"es_heap\", \"gridData\": {\"x\": 24, \"y\": 61, \"w\": 24, \"h\": 13, \"i\": \"es_heap\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Elasticsearch \\u2013 JVM heap used per node (% of max heap)\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-es_heap\", \"query\": {\"esql\": \"FROM metrics-elasticsearch.stack_monitoring.node_stats-*\\n| WHERE elasticsearch.node.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"elasticsearch\\\")\\n| STATS v = AVG(elasticsearch.node.stats.jvm.mem.heap.used.pct)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), elasticsearch.cluster.name, elasticsearch.node.name\\n| EVAL proc = CONCAT(elasticsearch.cluster.name, \\\" \\u00b7 \\\", elasticsearch.node.name),\\n       `Heap %` = ROUND(v, 1)\\n| KEEP t, `Heap %`, proc\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"Heap %\", \"fieldName\": \"Heap %\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"proc\", \"fieldName\": \"proc\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-es_heap\", \"title\": \"metrics-elasticsearch.stack_monitoring.node_stats-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"bottom\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"Heap %\"], \"splitAccessor\": \"proc\"}]}, \"query\": {\"esql\": \"FROM metrics-elasticsearch.stack_monitoring.node_stats-*\\n| WHERE elasticsearch.node.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"elasticsearch\\\")\\n| STATS v = AVG(elasticsearch.node.stats.jvm.mem.heap.used.pct)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), elasticsearch.cluster.name, elasticsearch.node.name\\n| EVAL proc = CONCAT(elasticsearch.cluster.name, \\\" \\u00b7 \\\", elasticsearch.node.name),\\n       `Heap %` = ROUND(v, 1)\\n| KEEP t, `Heap %`, proc\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-es_heap\": {\"id\": \"linux-obs-esql-es_heap\", \"title\": \"metrics-elasticsearch.stack_monitoring.node_stats-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-elasticsearch.stack_monitoring.node_stats-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"kb_mem\", \"gridData\": {\"x\": 0, \"y\": 74, \"w\": 24, \"h\": 13, \"i\": \"kb_mem\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Kibana \\u2013 memory (MB): RSS and V8 heap used\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-kb_mem\", \"query\": {\"esql\": \"FROM metrics-kibana.stack_monitoring.stats-*\\n| WHERE kibana.stats.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"kibana\\\")\\n| STATS rss = AVG(kibana.stats.process.memory.resident_set_size.bytes),\\n        heap = AVG(kibana.stats.process.memory.heap.used.bytes)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), proc = kibana.stats.name\\n| EVAL `RSS MB` = ROUND(rss / 1048576, 0),\\n       `Heap used MB` = ROUND(heap / 1048576, 0)\\n| KEEP t, `RSS MB`, `Heap used MB`, proc\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"RSS MB\", \"fieldName\": \"RSS MB\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"Heap used MB\", \"fieldName\": \"Heap used MB\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"proc\", \"fieldName\": \"proc\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-kb_mem\", \"title\": \"metrics-kibana.stack_monitoring.stats-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"bottom\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"RSS MB\", \"Heap used MB\"], \"splitAccessor\": \"proc\"}]}, \"query\": {\"esql\": \"FROM metrics-kibana.stack_monitoring.stats-*\\n| WHERE kibana.stats.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"kibana\\\")\\n| STATS rss = AVG(kibana.stats.process.memory.resident_set_size.bytes),\\n        heap = AVG(kibana.stats.process.memory.heap.used.bytes)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), proc = kibana.stats.name\\n| EVAL `RSS MB` = ROUND(rss / 1048576, 0),\\n       `Heap used MB` = ROUND(heap / 1048576, 0)\\n| KEEP t, `RSS MB`, `Heap used MB`, proc\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-kb_mem\": {\"id\": \"linux-obs-esql-kb_mem\", \"title\": \"metrics-kibana.stack_monitoring.stats-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-kibana.stack_monitoring.stats-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"kb_elu\", \"gridData\": {\"x\": 24, \"y\": 74, \"w\": 24, \"h\": 13, \"i\": \"kb_elu\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Kibana \\u2013 event loop utilization (%, CPU of the Node.js main thread)\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-kb_elu\", \"query\": {\"esql\": \"FROM metrics-kibana.stack_monitoring.stats-*\\n| WHERE kibana.stats.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"kibana\\\")\\n| STATS v = AVG(kibana.stats.process.event_loop_utilization.utilization)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), proc = kibana.stats.name\\n| EVAL `ELU %` = ROUND(v * 100, 1)\\n| KEEP t, `ELU %`, proc\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"ELU %\", \"fieldName\": \"ELU %\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"proc\", \"fieldName\": \"proc\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-kb_elu\", \"title\": \"metrics-kibana.stack_monitoring.stats-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"bottom\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"ELU %\"], \"splitAccessor\": \"proc\"}]}, \"query\": {\"esql\": \"FROM metrics-kibana.stack_monitoring.stats-*\\n| WHERE kibana.stats.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"kibana\\\")\\n| STATS v = AVG(kibana.stats.process.event_loop_utilization.utilization)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), proc = kibana.stats.name\\n| EVAL `ELU %` = ROUND(v * 100, 1)\\n| KEEP t, `ELU %`, proc\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-kb_elu\": {\"id\": \"linux-obs-esql-kb_elu\", \"title\": \"metrics-kibana.stack_monitoring.stats-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-kibana.stack_monitoring.stats-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"eskb_table\", \"gridData\": {\"x\": 0, \"y\": 87, \"w\": 48, \"h\": 9, \"i\": \"eskb_table\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Elasticsearch & Kibana nodes over the period (Stack Monitoring)\", \"type\": \"lens\", \"visualizationType\": \"lnsDatatable\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-eskb_table\", \"query\": {\"esql\": \"FROM metrics-elasticsearch.stack_monitoring.node_stats-*,metrics-kibana.stack_monitoring.stats-*\\n| WHERE elasticsearch.node.name IS NOT NULL OR kibana.stats.name IS NOT NULL  // hors erreurs de collecte\\n| EVAL component = CASE(elasticsearch.node.name IS NOT NULL, \\\"elasticsearch\\\", \\\"kibana\\\"),\\n       node = COALESCE(elasticsearch.node.name, kibana.stats.name),\\n       cpu = COALESCE(TO_DOUBLE(elasticsearch.node.stats.process.cpu.pct),\\n                      TO_DOUBLE(kibana.stats.process.event_loop_utilization.utilization) * 100),\\n       heap = COALESCE(TO_DOUBLE(elasticsearch.node.stats.jvm.mem.heap.used.bytes),\\n                       TO_DOUBLE(kibana.stats.process.memory.heap.used.bytes)),\\n       heap_pct = COALESCE(TO_DOUBLE(elasticsearch.node.stats.jvm.mem.heap.used.pct),\\n                           kibana.stats.process.memory.heap.used.bytes * 100.0\\n                             / kibana.stats.process.memory.heap.size_limit.bytes),\\n       rss = TO_DOUBLE(kibana.stats.process.memory.resident_set_size.bytes),\\n       eld = TO_DOUBLE(kibana.stats.process.event_loop_delay.ms)\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS cpu_avg = AVG(cpu), cpu_max = MAX(cpu), heap = AVG(heap), heap_pct = AVG(heap_pct),\\n        rss = AVG(rss), eld = AVG(eld), last_seen = MAX(@timestamp)\\n    BY component, node\\n| EVAL `CPU avg %` = ROUND(cpu_avg, 1),\\n       `CPU max %` = ROUND(cpu_max, 1),\\n       `Heap used MB` = ROUND(heap / 1048576, 0),\\n       `Heap %` = ROUND(heap_pct, 1),\\n       `RSS MB` = ROUND(rss / 1048576, 0),\\n       `Event loop delay ms` = ROUND(eld, 1),\\n       `Last metrics` = DATE_FORMAT(\\\"yyyy-MM-dd HH:mm:ss\\\", last_seen)\\n| RENAME component AS Process, node AS Node\\n| SORT Process, Node\\n| KEEP Process, Node, `CPU avg %`, `CPU max %`, `Heap used MB`, `Heap %`, `RSS MB`, `Event loop delay ms`, `Last metrics`\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"Process\", \"fieldName\": \"Process\", \"meta\": {\"type\": \"string\"}}, {\"columnId\": \"Node\", \"fieldName\": \"Node\", \"meta\": {\"type\": \"string\"}}, {\"columnId\": \"CPU avg %\", \"fieldName\": \"CPU avg %\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"CPU max %\", \"fieldName\": \"CPU max %\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"Heap used MB\", \"fieldName\": \"Heap used MB\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"Heap %\", \"fieldName\": \"Heap %\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"RSS MB\", \"fieldName\": \"RSS MB\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"Event loop delay ms\", \"fieldName\": \"Event loop delay ms\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"Last metrics\", \"fieldName\": \"Last metrics\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-eskb_table\", \"title\": \"metrics-elasticsearch.stack_monitoring.node_stats-*,metrics-kibana.stack_monitoring.stats-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"layerId\": \"layer1\", \"layerType\": \"data\", \"columns\": [{\"columnId\": \"Process\"}, {\"columnId\": \"Node\"}, {\"columnId\": \"CPU avg %\"}, {\"columnId\": \"CPU max %\"}, {\"columnId\": \"Heap used MB\"}, {\"columnId\": \"Heap %\"}, {\"columnId\": \"RSS MB\"}, {\"columnId\": \"Event loop delay ms\"}, {\"columnId\": \"Last metrics\"}]}, \"query\": {\"esql\": \"FROM metrics-elasticsearch.stack_monitoring.node_stats-*,metrics-kibana.stack_monitoring.stats-*\\n| WHERE elasticsearch.node.name IS NOT NULL OR kibana.stats.name IS NOT NULL  // hors erreurs de collecte\\n| EVAL component = CASE(elasticsearch.node.name IS NOT NULL, \\\"elasticsearch\\\", \\\"kibana\\\"),\\n       node = COALESCE(elasticsearch.node.name, kibana.stats.name),\\n       cpu = COALESCE(TO_DOUBLE(elasticsearch.node.stats.process.cpu.pct),\\n                      TO_DOUBLE(kibana.stats.process.event_loop_utilization.utilization) * 100),\\n       heap = COALESCE(TO_DOUBLE(elasticsearch.node.stats.jvm.mem.heap.used.bytes),\\n                       TO_DOUBLE(kibana.stats.process.memory.heap.used.bytes)),\\n       heap_pct = COALESCE(TO_DOUBLE(elasticsearch.node.stats.jvm.mem.heap.used.pct),\\n                           kibana.stats.process.memory.heap.used.bytes * 100.0\\n                             / kibana.stats.process.memory.heap.size_limit.bytes),\\n       rss = TO_DOUBLE(kibana.stats.process.memory.resident_set_size.bytes),\\n       eld = TO_DOUBLE(kibana.stats.process.event_loop_delay.ms)\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR component == ?component)\\n| STATS cpu_avg = AVG(cpu), cpu_max = MAX(cpu), heap = AVG(heap), heap_pct = AVG(heap_pct),\\n        rss = AVG(rss), eld = AVG(eld), last_seen = MAX(@timestamp)\\n    BY component, node\\n| EVAL `CPU avg %` = ROUND(cpu_avg, 1),\\n       `CPU max %` = ROUND(cpu_max, 1),\\n       `Heap used MB` = ROUND(heap / 1048576, 0),\\n       `Heap %` = ROUND(heap_pct, 1),\\n       `RSS MB` = ROUND(rss / 1048576, 0),\\n       `Event loop delay ms` = ROUND(eld, 1),\\n       `Last metrics` = DATE_FORMAT(\\\"yyyy-MM-dd HH:mm:ss\\\", last_seen)\\n| RENAME component AS Process, node AS Node\\n| SORT Process, Node\\n| KEEP Process, Node, `CPU avg %`, `CPU max %`, `Heap used MB`, `Heap %`, `RSS MB`, `Event loop delay ms`, `Last metrics`\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-eskb_table\": {\"id\": \"linux-obs-esql-eskb_table\", \"title\": \"metrics-elasticsearch.stack_monitoring.node_stats-*,metrics-kibana.stack_monitoring.stats-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-elasticsearch.stack_monitoring.node_stats-*,metrics-kibana.stack_monitoring.stats-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"ls_in\", \"gridData\": {\"x\": 0, \"y\": 96, \"w\": 24, \"h\": 14, \"i\": \"ls_in\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Logstash \\u2013 events IN per pipeline (events/s)\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-ls_in\", \"query\": {\"esql\": \"FROM metrics-logstash.pipeline-*\\n| WHERE logstash.pipeline.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"logstash\\\")\\n| STATS v = AVG(logstash.pipeline.total.flow.input_throughput.current)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, logstash.pipeline.name\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", logstash.pipeline.name),\\n       `IN events/s` = ROUND(v, 2)\\n| INLINE STATS avg_proc = AVG(`IN events/s`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `IN events/s`, proc\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"IN events/s\", \"fieldName\": \"IN events/s\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"proc\", \"fieldName\": \"proc\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-ls_in\", \"title\": \"metrics-logstash.pipeline-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"bottom\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"IN events/s\"], \"splitAccessor\": \"proc\"}]}, \"query\": {\"esql\": \"FROM metrics-logstash.pipeline-*\\n| WHERE logstash.pipeline.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"logstash\\\")\\n| STATS v = AVG(logstash.pipeline.total.flow.input_throughput.current)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, logstash.pipeline.name\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", logstash.pipeline.name),\\n       `IN events/s` = ROUND(v, 2)\\n| INLINE STATS avg_proc = AVG(`IN events/s`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `IN events/s`, proc\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-ls_in\": {\"id\": \"linux-obs-esql-ls_in\", \"title\": \"metrics-logstash.pipeline-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-logstash.pipeline-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"ls_out\", \"gridData\": {\"x\": 24, \"y\": 96, \"w\": 24, \"h\": 14, \"i\": \"ls_out\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Logstash \\u2013 events OUT per pipeline (events/s)\", \"type\": \"lens\", \"visualizationType\": \"lnsXY\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-ls_out\", \"query\": {\"esql\": \"FROM metrics-logstash.pipeline-*\\n| WHERE logstash.pipeline.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"logstash\\\")\\n| STATS v = AVG(logstash.pipeline.total.flow.output_throughput.current)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, logstash.pipeline.name\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", logstash.pipeline.name),\\n       `OUT events/s` = ROUND(v, 2)\\n| INLINE STATS avg_proc = AVG(`OUT events/s`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `OUT events/s`, proc\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"t\", \"fieldName\": \"t\", \"meta\": {\"type\": \"date\"}}, {\"columnId\": \"OUT events/s\", \"fieldName\": \"OUT events/s\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"proc\", \"fieldName\": \"proc\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-ls_out\", \"title\": \"metrics-logstash.pipeline-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"legend\": {\"isVisible\": true, \"position\": \"bottom\", \"shouldTruncate\": false}, \"valueLabels\": \"hide\", \"preferredSeriesType\": \"line\", \"fittingFunction\": \"Linear\", \"axisTitlesVisibilitySettings\": {\"x\": false, \"yLeft\": true, \"yRight\": true}, \"layers\": [{\"layerId\": \"layer1\", \"layerType\": \"data\", \"seriesType\": \"line\", \"xAccessor\": \"t\", \"accessors\": [\"OUT events/s\"], \"splitAccessor\": \"proc\"}]}, \"query\": {\"esql\": \"FROM metrics-logstash.pipeline-*\\n| WHERE logstash.pipeline.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"logstash\\\")\\n| STATS v = AVG(logstash.pipeline.total.flow.output_throughput.current)\\n    BY t = BUCKET(@timestamp, 75, ?_tstart, ?_tend), host.name, logstash.pipeline.name\\n| EVAL proc = CONCAT(host.name, \\\" \\u00b7 \\\", logstash.pipeline.name),\\n       `OUT events/s` = ROUND(v, 2)\\n| INLINE STATS avg_proc = AVG(`OUT events/s`) BY proc\\n| INLINE STATS avgs = VALUES(avg_proc)\\n| EVAL cutoff = MV_MIN(MV_SLICE(MV_SORT(avgs, \\\"DESC\\\"), 0, 9))\\n| WHERE avg_proc >= cutoff\\n| KEEP t, `OUT events/s`, proc\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-ls_out\": {\"id\": \"linux-obs-esql-ls_out\", \"title\": \"metrics-logstash.pipeline-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-logstash.pipeline-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}, {\"type\": \"lens\", \"panelIndex\": \"ls_table\", \"gridData\": {\"x\": 0, \"y\": 110, \"w\": 48, \"h\": 13, \"i\": \"ls_table\"}, \"embeddableConfig\": {\"attributes\": {\"title\": \"Logstash pipelines over the period\", \"type\": \"lens\", \"visualizationType\": \"lnsDatatable\", \"references\": [], \"state\": {\"datasourceStates\": {\"textBased\": {\"layers\": {\"layer1\": {\"index\": \"linux-obs-esql-ls_table\", \"query\": {\"esql\": \"FROM metrics-logstash.pipeline-*\\n| WHERE logstash.pipeline.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"logstash\\\")\\n| STATS in_eps = AVG(logstash.pipeline.total.flow.input_throughput.current),\\n        out_eps = AVG(logstash.pipeline.total.flow.output_throughput.current),\\n        workers = AVG(logstash.pipeline.total.flow.worker_utilization.current),\\n        backpressure = AVG(logstash.pipeline.total.flow.queue_backpressure.current),\\n        last_seen = MAX(@timestamp)\\n    BY host.name, logstash.pipeline.name\\n| EVAL `IN events/s` = ROUND(in_eps, 2),\\n       `OUT events/s` = ROUND(out_eps, 2),\\n       `Workers busy %` = ROUND(workers, 1),\\n       `Backpressure` = ROUND(backpressure, 3),\\n       `Last metrics` = DATE_FORMAT(\\\"yyyy-MM-dd HH:mm:ss\\\", last_seen)\\n| RENAME host.name AS Host, logstash.pipeline.name AS Pipeline\\n| SORT Host, Pipeline\\n| KEEP Host, Pipeline, `IN events/s`, `OUT events/s`, `Workers busy %`, `Backpressure`, `Last metrics`\"}, \"timeField\": \"@timestamp\", \"columns\": [{\"columnId\": \"Host\", \"fieldName\": \"Host\", \"meta\": {\"type\": \"string\"}}, {\"columnId\": \"Pipeline\", \"fieldName\": \"Pipeline\", \"meta\": {\"type\": \"string\"}}, {\"columnId\": \"IN events/s\", \"fieldName\": \"IN events/s\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"OUT events/s\", \"fieldName\": \"OUT events/s\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"Workers busy %\", \"fieldName\": \"Workers busy %\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"Backpressure\", \"fieldName\": \"Backpressure\", \"meta\": {\"type\": \"number\"}}, {\"columnId\": \"Last metrics\", \"fieldName\": \"Last metrics\", \"meta\": {\"type\": \"string\"}}]}}, \"indexPatternRefs\": [{\"id\": \"linux-obs-esql-ls_table\", \"title\": \"metrics-logstash.pipeline-*\", \"timeField\": \"@timestamp\"}]}}, \"visualization\": {\"layerId\": \"layer1\", \"layerType\": \"data\", \"columns\": [{\"columnId\": \"Host\"}, {\"columnId\": \"Pipeline\"}, {\"columnId\": \"IN events/s\"}, {\"columnId\": \"OUT events/s\"}, {\"columnId\": \"Workers busy %\"}, {\"columnId\": \"Backpressure\"}, {\"columnId\": \"Last metrics\"}]}, \"query\": {\"esql\": \"FROM metrics-logstash.pipeline-*\\n| WHERE logstash.pipeline.name IS NOT NULL\\n| WHERE (?os_type == \\\"All\\\" OR ?os_type == \\\"linux\\\") AND (?component == \\\"All\\\" OR ?component == \\\"logstash\\\")\\n| STATS in_eps = AVG(logstash.pipeline.total.flow.input_throughput.current),\\n        out_eps = AVG(logstash.pipeline.total.flow.output_throughput.current),\\n        workers = AVG(logstash.pipeline.total.flow.worker_utilization.current),\\n        backpressure = AVG(logstash.pipeline.total.flow.queue_backpressure.current),\\n        last_seen = MAX(@timestamp)\\n    BY host.name, logstash.pipeline.name\\n| EVAL `IN events/s` = ROUND(in_eps, 2),\\n       `OUT events/s` = ROUND(out_eps, 2),\\n       `Workers busy %` = ROUND(workers, 1),\\n       `Backpressure` = ROUND(backpressure, 3),\\n       `Last metrics` = DATE_FORMAT(\\\"yyyy-MM-dd HH:mm:ss\\\", last_seen)\\n| RENAME host.name AS Host, logstash.pipeline.name AS Pipeline\\n| SORT Host, Pipeline\\n| KEEP Host, Pipeline, `IN events/s`, `OUT events/s`, `Workers busy %`, `Backpressure`, `Last metrics`\"}, \"filters\": [], \"internalReferences\": [], \"adHocDataViews\": {\"linux-obs-esql-ls_table\": {\"id\": \"linux-obs-esql-ls_table\", \"title\": \"metrics-logstash.pipeline-*\", \"timeFieldName\": \"@timestamp\", \"sourceFilters\": [], \"type\": \"esql\", \"fieldFormats\": {}, \"runtimeFieldMap\": {}, \"allowNoIndex\": false, \"name\": \"metrics-logstash.pipeline-*\", \"allowHidden\": false}}, \"needsRefresh\": false}}, \"enhancements\": {}}}]", "refreshInterval": {"pause": false, "value": 60000}, "timeFrom": "now-1h", "timeRestore": true, "timeTo": "now", "title": "Stack – Process observability v1 (Linux / Windows)"}, "coreMigrationVersion": "8.8.0", "created_at": "2026-10-31T17:05:42.936Z", "id": "stack-observability-v1", "managed": false, "references": [{"id": "linux-obs-metrics", "name": "controlGroup_host:optionsListDataView", "type": "index-pattern"}], "type": "dashboard", "typeMigrationVersion": "10.3.0", "updated_at": "2026-10-31T17:05:42.936Z", "version": "WzcyNiw0XQ=="}
```

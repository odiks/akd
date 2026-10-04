# Règles d'alerting Kibana – Logstash (elk-lab)

Ce document décrit les règles d'alerting Kibana préfixées `[P1]`, `[P2]` et `[P2/P1]`. Il donne
pour chacune sa requête, ses seuils, ses actions et le JSON prêt à envoyer à l'API, ainsi que les
prérequis pour les recréer dans un autre Kibana.

- Testé sur **Elasticsearch / Kibana 9.5.4**.
- Document généré par `alerting/generate-doc.sh` à partir des fichiers déployés
  (`alerting/rules/*.json`) : ne pas l'éditer à la main.

## Priorités

| Préfixe | Signification |
|---|---|
| `[P1]` | Astreinte immédiate : perte ou arrêt de flux en cours ou imminent |
| `[P2]` | À traiter dans la journée : surveillance dégradée, risque sans impact immédiat |
| `[P2/P1]` | Règle à deux niveaux : **WARNING = P2**, **CRITICAL = P1** |

La priorité figure dans le nom de la règle, dans ses tags (`P1` / `P2`) et dans le champ `priorite`
des notifications webhook.

## Sommaire

| ID | Nom | Type | Intervalle | Fenêtre | Confirmations (`alert_delay`) | État dans le lab |
|---|---|---|---|---|---|---|
| [`kb-ls-p1-es-unreachable`](#kb-ls-p1-es-unreachable) | [P1] Logstash – Elasticsearch injoignable | Elasticsearch query (ES\|QL) | 1m | 2 m | 2 | **active** |
| [`kb-ls-p1-heap`](#kb-ls-p1-heap) | [P1] Logstash – heap JVM critique (> 95 % sur 10 min) | Elasticsearch query (ES\|QL) | 1m | 10 m | 1 | **active** |
| [`kb-ls-p1-index-rejected`](#kb-ls-p1-index-rejected) | [P1] Logstash – événements rejetés par Elasticsearch (perte de données) | Elasticsearch query (ES\|QL) | 1m | 5 m | 1 | **active** |
| [`kb-ls-p1-oom`](#kb-ls-p1-oom) | [P1] Logstash – OutOfMemoryError | Elasticsearch query (ES\|QL) | 1m | 5 m | 1 | **active** |
| [`kb-ls-p1-p2p-broken`](#kb-ls-p1-p2p-broken) | [P1] Logstash – chaîne _in → _out coupée | Elasticsearch query (ES\|QL) | 1m | 2 m | 2 | **active** |
| [`kb-ls-p1-pipeline-not-running`](#kb-ls-p1-pipeline-not-running) | [P1] Logstash – pipeline arrêté ou en erreur (health report) | Elasticsearch query (ES\|QL) | 1m | 3 m | 3 | **active** |
| [`kb-ls-p1-pool-down`](#kb-ls-p1-pool-down) | [P1] Logstash – pool sans aucun nœud sain | Elasticsearch query (ES\|QL) | 1m | 60 m | 2 | **active** |
| [`kb-ls-p1-port-in-use`](#kb-ls-p1-port-in-use) | [P1] Logstash – port d’écoute déjà utilisé | Elasticsearch query (ES\|QL) | 1m | 5 m | 1 | **active** |
| [`kb-ls-p1-process-absent`](#kb-ls-p1-process-absent) | [P1] Logstash – process absent | Elasticsearch query (ES\|QL) | 1m | 60 m | 2 | **active** |
| [`kb-ls-p1-restart-loop`](#kb-ls-p1-restart-loop) | [P1] Logstash – démarrages en boucle | Elasticsearch query (ES\|QL) | 1m | 10 m | 1 | **active** |
| [`kb-ls-p1-shutdown-stalled`](#kb-ls-p1-shutdown-stalled) | [P1] Logstash – arrêt de pipeline figé | Elasticsearch query (ES\|QL) | 1m | 5 m | 1 | **active** |
| [`kb-synth-p1-input-down`](#kb-synth-p1-input-down) | [P1] Logstash – input réseau injoignable (Synthetics) | Elasticsearch query (ES\|QL) | 1m | 60 m | 2 | **active** |
| [`logstash-disk`](#logstash-disk) | [P2/P1] Logstash – disque des nœuds (files persistantes) | Custom threshold | 1m | 5 m | 2 | **active** |
| [`logstash-queue-fill-pq`](#logstash-queue-fill-pq) | [P2/P1] Logstash – remplissage file persistante (WARNING 20 % / CRITICAL 50 %) | Custom threshold | 1m | 2 m | 2 | **active** |
| [`logstash-queue-saturation-memory`](#logstash-queue-saturation-memory) | [P2/P1] Logstash – saturation file mémoire (WARNING 0,2 / CRITICAL 0,5 de contre-pression) | Custom threshold | 1m | 2 m | 2 | **active** |
| [`kb-synth-stale`](#kb-synth-stale) | [P2] Synthetics – sonde muette (moniteurs des inputs non exécutés) | Elasticsearch query (ES\|QL) | 1m | 60 m | 2 | **active** |
| [`logstash-pq-time-to-full`](#logstash-pq-time-to-full) | [P1] Logstash – file persistante pleine dans moins de 30 min | Elasticsearch query (ES\|QL) | 1m | 3 m | 2 | désactivée |
| [`logstash-pq-fill`](#logstash-pq-fill) | [P2/P1] Logstash – remplissage de la file persistante | Custom threshold | 1m | 2 m | 2 | désactivée |
| [`logstash-backpressure`](#logstash-backpressure) | [P2] Logstash – contre-pression prolongée sur un pipeline | Custom threshold | 1m | 5 m | 2 | désactivée |
| [`logstash-metrics-stale`](#logstash-metrics-stale) | [P2] Logstash – métriques de pipeline absentes (nœud ou agent muet) | Elasticsearch query (ES\|QL) | 1m | 1 h | 2 | désactivée |

Les règles ES|QL créent **une alerte par ligne de résultat** (`groupBy: row`) : le seuil « plus de
0 ligne » signifie « au moins un nœud, pipeline ou moniteur en anomalie ». Le filtre de temps de la
fenêtre est appliqué par Kibana sur `@timestamp`, en plus de la requête.

## 1. Prérequis dans le cluster cible

### 1.1 Données collectées (Elastic Agent / Fleet)

| Source | Data streams | Utilisés par |
|---|---|---|
| Intégration **System** sur les nœuds Logstash | `metrics-system.process-*`, `metrics-system.filesystem-*`, `metrics-system.*` | process absent, disque, ancre de fraîcheur (pool, sonde muette) |
| Intégration **Logstash** (2.11.x), métriques par l'API Logstash (cel) | `metrics-logstash.node-*`, `metrics-logstash.pipeline-*`, `metrics-logstash.health_report-*` | heap, files, pool, health report |
| Intégration **Logstash**, logs | `logs-logstash.log-*` (dataset `logstash.log`) | règles basées sur les logs |
| **Synthetics**, emplacement privé | `synthetics-tcp-*`, `synthetics-http-*` | règles `kb-synth-*` |

Les nœuds Logstash doivent s'appeler `logstash-*` (`host.name`) : plusieurs requêtes filtrent sur
`host.name LIKE "logstash-*"`. **Adapter ce motif** si les noms sont différents.

### 1.2 Champ `logstash.pool`

Ce champ est nécessaire à `kb-ls-p1-pool-down`.
- Il est ajouté par chaque agent policy de pool :
  `"global_data_tags": [{"name": "logstash.pool", "value": "pool1"}]`.
- Les data streams Logstash ont `dynamic: false` : il faut donc mapper ce champ.

```bash
PUT _component_template/logstash@custom
{ "template": { "mappings": { "properties": { "logstash": { "properties": { "pool": { "type": "keyword" } } } } } } }

# Data streams déjà existants
PUT metrics-logstash.*,logs-logstash.*/_mapping?allow_no_indices=true
{ "properties": { "logstash": { "properties": { "pool": { "type": "keyword" } } } } }
```

### 1.3 Moniteurs Synthetics (règles `kb-synth-*`)

- **Convention de nom obligatoire** : `<pipeline> · <nœud> · <proto>/<port>`, par exemple
  `pool1-syslog_in · logstash-1 · tcp/5514`. La règle en extrait le pipeline et le nœud avec
  `DISSECT`.
- **Tag `input`** sur chaque moniteur d'input.
- **IPv4 forcé** (`ipv6: false`) si le réseau de l'emplacement privé n'a pas d'IPv6.
- Pour un input `http` protégé par mot de passe : moniteur HTTP sans identifiants avec
  `check.response.status: ["401"]`, ce qui ne crée aucun événement dans le pipeline.
- Voir `synthetics/monitors/*.json`.

### 1.4 Vues de données (règles custom threshold)

Les règles custom threshold désignent leur vue de données **par son ID**
(`params.searchConfiguration.index`). Créer ces vues avec les mêmes ID, ou remplacer l'ID dans les
règles.

```bash
POST kbn:/api/data_views/data_view
{
  "data_view": {
    "id": "lab-dv-logstash-pipeline",
    "name": "Logstash – métriques par pipeline",
    "title": "metrics-logstash.pipeline-*",
    "timeFieldName": "@timestamp"
  },
  "override": true
}

POST kbn:/api/data_views/data_view
{
  "data_view": {
    "id": "lab-dv-system-filesystem",
    "name": "Système – systèmes de fichiers",
    "title": "metrics-system.filesystem-*",
    "timeFieldName": "@timestamp"
  },
  "override": true
}

```

### 1.5 Connecteur webhook

Toutes les règles notifient le connecteur d'ID **`lab-webhook-logstash`**. Le recréer avec cet ID,
ou remplacer `actions[].id` dans les règles.

```bash
POST kbn:/api/actions/connector/lab-webhook-logstash
{
  "connector_type_id": ".webhook",
  "name": "Webhook lab → Logstash pool2 (logs-webhook-lab)",
  "config": {
    "url": "<URL_DU_WEBHOOK>",
    "method": "post",
    "headers": {
      "Content-Type": "application/json"
    },
    "hasAuth": true,
    "authType": "webhook-authentication-basic"
  },
  "secrets": {
    "user": "webhook",
    "password": "<MOT_DE_PASSE>"
  }
}
```

Le corps des notifications est un JSON avec les champs `source`, `severite`, `priorite`, `regle`,
`alert_id`, `titre`, `raison`, `date` et `lien`, plus des champs propres à certaines règles.

## 2. Recréer les règles

### Par l'API (recommandé)

Copier le JSON de chaque règle (sections ci-dessous, ou `alerting/rules/<id>.json`) puis :

```bash
KIBANA=https://kibana.exemple:5601
for f in alerting/rules/*.json; do
  id=$(basename "$f" .json)
  curl -sS -u "elastic:$PASSWORD" -H 'kbn-xsrf: true' -H 'Content-Type: application/json' \
    -X POST "$KIBANA/api/alerting/rule/$id" -d @"$f"
done
# Désactiver ensuite celles qui ne doivent pas tourner :
#   POST $KIBANA/api/alerting/rule/<id>/_disable
```

- **Mise à jour** d'une règle existante : `PUT /api/alerting/rule/<id>` avec le même JSON **sans**
  `rule_type_id` ni `consumer`, qui ne sont pas modifiables.
- Dans le lab, `alerting/deploy.sh` fait tout cela, vues de données et connecteur compris.

### Par l'interface

Stack Management → Rules → Create rule :
- **ES|QL** : type *Elasticsearch query*, *ES|QL*. Coller la requête, puis régler la fenêtre
  (*Time window*), *Alert group* = « Create an alert for each row », et *Advanced options* → *Alert
  delay* = nombre de confirmations.
- **Custom threshold** : type *Custom threshold* (Observability). Choisir la vue de données, le
  filtre KQL, les agrégations et l'équation, les seuils, *Group alerts by*, puis décocher
  *Alert me if there's no data*.
- **Actions** : connecteur webhook ; un corps par groupe d'actions (voir le JSON de chaque règle).

### Afficher le pipeline dans l'onglet Alerts

Toutes les règles regroupent sur les mêmes noms de champs grâce aux alias ES|QL
(`BY host.name = logstash.node.name, logstash.pipeline.name = logstash.log.pipeline_id`). Dans
Stack Management → Alerts, ou dans une règle → onglet Alerts, ajouter avec **Fields** les colonnes :
- `kibana.alert.grouping.logstash.pipeline.name` : pipeline ;
- `kibana.alert.grouping.host.name` : nœud ;
- `kibana.alert.rule.tags` : priorité ;
- `kibana.alert.severity` : warning ou critical (custom threshold).

## 3. Pièges connus

- **`LIKE` ou `CASE` dans `STATS`** : l'éditeur ES|QL de Kibana refuse (« Function LIKE not allowed
  in STATS ») alors qu'Elasticsearch l'exécute. Calculer la valeur dans un `EVAL` avant le `STATS`.
- **`last` est un mot réservé** en ES|QL (`STATS last = …` provoque une erreur de parsing).
- **`INLINE STATS`** (ancre de fraîcheur) et **`QSTR`** demandent un ES|QL récent (testé en 9.5.4).
- **`host.name` n'est pas indexé** dans `metrics-logstash.health_report` (`dynamic: false`) : utiliser
  `logstash.node.name`.
- **Lucene** : `(NOT a OR b)` se lit `b AND NOT a`. L'analyseur standard garde
  `java.lang.OutOfMemoryError` comme un seul mot, d'où les deux formes dans la requête OOM.
- **File persistante** : `current_size` inclut la page en cours déjà acquittée. Avec des pages de
  64 Mo et `queue.max_bytes` à 100 Mo, la file affiche jusqu'à 64 % sans événement en attente.
  D'où le filtre `queues.events > 1000`, et la recommandation d'une `queue.page_capacity` d'au plus
  `max_bytes / 4`.
- **File mémoire** : Logstash publie `events = 0` et `max_size = 0`. Pas de pourcentage possible ;
  on utilise la contre-pression (`flow.queue_backpressure`).
- **Custom threshold** : ne jamais combiner `groupBy` et *Alert on no data*, qui produit de fausses
  alertes « nodata » sur le groupe `*`. L'absence de données se surveille avec une règle ES|QL.
- **Défaut connu de `kb-ls-p1-pipeline-not-running`** : les relevés `LOADING` du démarrage restent 3
  minutes dans la fenêtre, d'où une fausse P1 à chaque démarrage de Logstash.
  Correctif : ne garder que le dernier relevé par nœud et pipeline
  (`INLINE STATS last_ts = MAX(@timestamp) BY … | WHERE @timestamp == last_ts`).
- **Message des custom threshold** : la valeur s'affiche avec l'unité du champ (« 60.2 B ») alors
  qu'il s'agit d'un pourcentage.

## 4. Détail des règles

### kb-ls-p1-es-unreachable

**[P1] Logstash – Elasticsearch injoignable**

« Elasticsearch Unreachable » ou « Marking url as dead » : Logstash n'atteint plus Elasticsearch.

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Fenêtre de temps | 2 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM logs-logstash.log-*
| WHERE data_stream.dataset == "logstash.log" AND QSTR("message:\"Elasticsearch Unreachable\" OR message:\"Marking url as dead\"")
| STATS erreurs = COUNT(*) BY host.name
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-ls-p1-es-unreachable)</summary>

```json
{
  "name": "[P1] Logstash – Elasticsearch injoignable",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM logs-logstash.log-* | WHERE data_stream.dataset == \"logstash.log\" AND QSTR(\"message:\\\"Elasticsearch Unreachable\\\" OR message:\\\"Marking url as dead\\\"\") | STATS erreurs = COUNT(*) BY host.name"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 2,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### kb-ls-p1-heap

**[P1] Logstash – heap JVM critique (> 95 % sur 10 min)**

Heap JVM moyen supérieur à 95 % sur 10 min : GC permanent, OutOfMemoryError probable.

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 1 |
| Fenêtre de temps | 10 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM metrics-logstash.node-*
| STATS heap_pct = ROUND(AVG(logstash.node.stats.jvm.mem.heap_used_percent), 1) BY host.name
| WHERE heap_pct > 95
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-ls-p1-heap)</summary>

```json
{
  "name": "[P1] Logstash – heap JVM critique (> 95 % sur 10 min)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 1
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM metrics-logstash.node-* | STATS heap_pct = ROUND(AVG(logstash.node.stats.jvm.mem.heap_used_percent), 1) BY host.name | WHERE heap_pct > 95"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 10,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### kb-ls-p1-index-rejected

**[P1] Logstash – événements rejetés par Elasticsearch (perte de données)**

« Could not index event » : Elasticsearch rejette définitivement des documents (perte de données sans DLQ).

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 1 |
| Fenêtre de temps | 5 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM logs-logstash.log-*
| WHERE data_stream.dataset == "logstash.log" AND QSTR("message:\"Could not index event\"")
| STATS rejets = COUNT(*) BY host.name, logstash.pipeline.name = logstash.log.pipeline_id
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-ls-p1-index-rejected)</summary>

```json
{
  "name": "[P1] Logstash – événements rejetés par Elasticsearch (perte de données)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 1
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM logs-logstash.log-* | WHERE data_stream.dataset == \"logstash.log\" AND QSTR(\"message:\\\"Could not index event\\\"\") | STATS rejets = COUNT(*) BY host.name, logstash.pipeline.name = logstash.log.pipeline_id"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 5,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### kb-ls-p1-oom

**[P1] Logstash – OutOfMemoryError**

`OutOfMemoryError` dans les logs Logstash (5 dernières minutes).

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 1 |
| Fenêtre de temps | 5 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM logs-logstash.log-*
| WHERE data_stream.dataset == "logstash.log" AND QSTR("message:\"java.lang.OutOfMemoryError\" OR message:\"OutOfMemoryError\"")
| STATS occurrences = COUNT(*) BY host.name
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-ls-p1-oom)</summary>

```json
{
  "name": "[P1] Logstash – OutOfMemoryError",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 1
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM logs-logstash.log-* | WHERE data_stream.dataset == \"logstash.log\" AND QSTR(\"message:\\\"java.lang.OutOfMemoryError\\\" OR message:\\\"OutOfMemoryError\\\"\") | STATS occurrences = COUNT(*) BY host.name"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 5,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### kb-ls-p1-p2p-broken

**[P1] Logstash – chaîne _in → _out coupée**

Plus de 30 « address was unavailable » en 2 min : le pipeline `_in` n'arrive plus à envoyer vers son `_out` (pipeline-to-pipeline).

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Fenêtre de temps | 2 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM logs-logstash.log-*
| WHERE data_stream.dataset == "logstash.log" AND QSTR("message:\"address was unavailable\"")
| STATS tentatives = COUNT(*) BY host.name, logstash.pipeline.name = logstash.log.pipeline_id
| WHERE tentatives > 30
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-ls-p1-p2p-broken)</summary>

```json
{
  "name": "[P1] Logstash – chaîne _in → _out coupée",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM logs-logstash.log-* | WHERE data_stream.dataset == \"logstash.log\" AND QSTR(\"message:\\\"address was unavailable\\\"\") | STATS tentatives = COUNT(*) BY host.name, logstash.pipeline.name = logstash.log.pipeline_id | WHERE tentatives > 30"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 2,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### kb-ls-p1-pipeline-not-running

**[P1] Logstash – pipeline arrêté ou en erreur (health report)**

Health report Logstash : un pipeline est `FINISHED`, `TERMINATED` ou `LOADING`, ou son statut est `red` (fenêtre de 3 min). L'état `UNKNOWN` est exclu (pipelines supprimés).

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 3 |
| Fenêtre de temps | 3 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM metrics-logstash.health_report-*
| WHERE logstash.pipeline.state IN ("FINISHED", "TERMINATED", "LOADING") OR logstash.pipeline.status == "red"
| STATS rapports = COUNT(*), etat = MAX(logstash.pipeline.state), statut = MAX(logstash.pipeline.status) BY host.name = logstash.node.name, logstash.pipeline.name = logstash.pipeline.id
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-ls-p1-pipeline-not-running)</summary>

```json
{
  "name": "[P1] Logstash – pipeline arrêté ou en erreur (health report)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 3
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM metrics-logstash.health_report-* | WHERE logstash.pipeline.state IN (\"FINISHED\", \"TERMINATED\", \"LOADING\") OR logstash.pipeline.status == \"red\" | STATS rapports = COUNT(*), etat = MAX(logstash.pipeline.state), statut = MAX(logstash.pipeline.status) BY host.name = logstash.node.name, logstash.pipeline.name = logstash.pipeline.id"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 3,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### kb-ls-p1-pool-down

**[P1] Logstash – pool sans aucun nœud sain**

Aucun nœud d'un pool ne remonte de métriques de pipeline depuis plus de 180 s, par rapport aux autres hôtes Logstash.

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Fenêtre de temps | 60 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM metrics-logstash.pipeline-*,metrics-system.*
| WHERE host.name LIKE "logstash-*"
| INLINE STATS anchor = MAX(@timestamp)
| WHERE data_stream.dataset == "logstash.pipeline"
| STATS last_seen = MAX(@timestamp), anchor = MAX(anchor) BY logstash.pool
| EVAL retard_s = DATE_DIFF("second", last_seen, anchor)
| WHERE retard_s > 180
| KEEP logstash.pool, last_seen, retard_s
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-ls-p1-pool-down)</summary>

```json
{
  "name": "[P1] Logstash – pool sans aucun nœud sain",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM metrics-logstash.pipeline-*,metrics-system.* | WHERE host.name LIKE \"logstash-*\" | INLINE STATS anchor = MAX(@timestamp) | WHERE data_stream.dataset == \"logstash.pipeline\" | STATS last_seen = MAX(@timestamp), anchor = MAX(anchor) BY logstash.pool | EVAL retard_s = DATE_DIFF(\"second\", last_seen, anchor) | WHERE retard_s > 180 | KEEP logstash.pool, last_seen, retard_s"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 60,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### kb-ls-p1-port-in-use

**[P1] Logstash – port d’écoute déjà utilisé**

« Address already in use » : un input n'a pas pu ouvrir son port, le pipeline ne démarre pas.

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 1 |
| Fenêtre de temps | 5 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM logs-logstash.log-*
| WHERE data_stream.dataset == "logstash.log" AND QSTR("message:\"Address already in use\"")
| STATS occurrences = COUNT(*) BY host.name, logstash.pipeline.name = logstash.log.pipeline_id
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-ls-p1-port-in-use)</summary>

```json
{
  "name": "[P1] Logstash – port d’écoute déjà utilisé",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 1
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM logs-logstash.log-* | WHERE data_stream.dataset == \"logstash.log\" AND QSTR(\"message:\\\"Address already in use\\\"\") | STATS occurrences = COUNT(*) BY host.name, logstash.pipeline.name = logstash.log.pipeline_id"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 5,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### kb-ls-p1-process-absent

**[P1] Logstash – process absent**

Le process java `org.logstash.Logstash` n'est plus vu sur un nœud depuis plus de 120 s, alors que les autres hôtes Logstash remontent encore leurs métriques. Se déclenche aussi si l'agent du nœud est muet.

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Fenêtre de temps | 60 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM metrics-system.*
| WHERE host.name LIKE "logstash-*"
| INLINE STATS anchor = MAX(@timestamp)
| WHERE data_stream.dataset == "system.process"
| EVAL ls_ts = CASE(process.command_line LIKE "*org.logstash.Logstash*", @timestamp, NULL)
| STATS last_ls = MAX(ls_ts), anchor = MAX(anchor) BY host.name
| EVAL retard_s = DATE_DIFF("second", COALESCE(last_ls, NOW() - 1 hour), anchor)
| WHERE retard_s > 120
| EVAL dernier_vu = COALESCE(TO_STRING(last_ls), "aucun dans la dernière heure")
| KEEP host.name, last_ls, dernier_vu, retard_s
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-ls-p1-process-absent)</summary>

```json
{
  "name": "[P1] Logstash – process absent",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM metrics-system.* | WHERE host.name LIKE \"logstash-*\" | INLINE STATS anchor = MAX(@timestamp) | WHERE data_stream.dataset == \"system.process\" | EVAL ls_ts = CASE(process.command_line LIKE \"*org.logstash.Logstash*\", @timestamp, NULL) | STATS last_ls = MAX(ls_ts), anchor = MAX(anchor) BY host.name | EVAL retard_s = DATE_DIFF(\"second\", COALESCE(last_ls, NOW() - 1 hour), anchor) | WHERE retard_s > 120 | EVAL dernier_vu = COALESCE(TO_STRING(last_ls), \"aucun dans la dernière heure\") | KEEP host.name, last_ls, dernier_vu, retard_s"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 60,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{alert.id}} : process Logstash absent depuis {{#context.hits}}{{_source.retard_s}}{{/context.hits}} s\",\"raison\":\"Aucun process org.logstash.Logstash sur {{alert.id}} depuis {{#context.hits}}{{_source.retard_s}}{{/context.hits}} s (dernier vu : {{#context.hits}}{{_source.dernier_vu}}{{/context.hits}}). Vérifier : systemctl status logstash, journalctl -u logstash. Si l agent du nœud est lui-même muet, l alerte se déclenche aussi.\",\"retard_s\":\"{{#context.hits}}{{_source.retard_s}}{{/context.hits}}\",\"dernier_process\":\"{{#context.hits}}{{_source.dernier_vu}}{{/context.hits}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### kb-ls-p1-restart-loop

**[P1] Logstash – démarrages en boucle**

Plus de 2 démarrages de Logstash (`Starting Logstash`) en 10 min sur un nœud : crash ou redémarrage en boucle.

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 1 |
| Fenêtre de temps | 10 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM logs-logstash.log-*
| WHERE data_stream.dataset == "logstash.log" AND QSTR("message:\"Starting Logstash\"")
| STATS demarrages = COUNT(*) BY host.name
| WHERE demarrages > 2
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-ls-p1-restart-loop)</summary>

```json
{
  "name": "[P1] Logstash – démarrages en boucle",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 1
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM logs-logstash.log-* | WHERE data_stream.dataset == \"logstash.log\" AND QSTR(\"message:\\\"Starting Logstash\\\"\") | STATS demarrages = COUNT(*) BY host.name | WHERE demarrages > 2"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 10,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### kb-ls-p1-shutdown-stalled

**[P1] Logstash – arrêt de pipeline figé**

« shutdown process appears to be stalled » : un pipeline n'arrive pas à s'arrêter (workers bloqués).

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 1 |
| Fenêtre de temps | 5 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM logs-logstash.log-*
| WHERE data_stream.dataset == "logstash.log" AND QSTR("message:\"shutdown process appears to be stalled\"")
| STATS occurrences = COUNT(*) BY host.name
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-ls-p1-shutdown-stalled)</summary>

```json
{
  "name": "[P1] Logstash – arrêt de pipeline figé",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 1
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM logs-logstash.log-* | WHERE data_stream.dataset == \"logstash.log\" AND QSTR(\"message:\\\"shutdown process appears to be stalled\\\"\") | STATS occurrences = COUNT(*) BY host.name"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 5,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### kb-synth-p1-input-down

**[P1] Logstash – input réseau injoignable (Synthetics)**

Moniteur Synthetics d'un input réseau (tag `input`) : toutes les vérifications des 3 dernières minutes sont en échec (au moins 2).

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `synthetics`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Fenêtre de temps | 60 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM synthetics-tcp-*,synthetics-http-*
| WHERE summary.up IS NOT NULL AND QSTR("tags:input")
| DISSECT monitor.name "%{pipeline} · %{noeud} · %{port}"
| EVAL recent = CASE(@timestamp >= NOW() - 3 minutes, 1, 0), down_recent = CASE(@timestamp >= NOW() - 3 minutes AND monitor.status == "down", 1, 0), up_ts = CASE(monitor.status == "up", @timestamp, NULL), err = CASE(@timestamp >= NOW() - 3 minutes, TO_STRING(error.message), NULL)
| STATS verifs = SUM(recent), echecs = SUM(down_recent), dernier_up = MAX(up_ts), erreur = MAX(err), cible = MAX(url.full) BY host.name = noeud, logstash.pipeline.name = pipeline, port, monitor.name, observer.geo.name
| EVAL dernier_succes = COALESCE(TO_STRING(dernier_up), "aucun dans la dernière heure"), erreur = COALESCE(erreur, "inconnue")
| WHERE verifs >= 2 AND echecs == verifs
| KEEP host.name, logstash.pipeline.name, port, monitor.name, observer.geo.name, cible, verifs, echecs, dernier_succes, erreur
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | critical | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-synth-p1-input-down)</summary>

```json
{
  "name": "[P1] Logstash – input réseau injoignable (Synthetics)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "synthetics",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM synthetics-tcp-*,synthetics-http-* | WHERE summary.up IS NOT NULL AND QSTR(\"tags:input\") | DISSECT monitor.name \"%{pipeline} · %{noeud} · %{port}\" | EVAL recent = CASE(@timestamp >= NOW() - 3 minutes, 1, 0), down_recent = CASE(@timestamp >= NOW() - 3 minutes AND monitor.status == \"down\", 1, 0), up_ts = CASE(monitor.status == \"up\", @timestamp, NULL), err = CASE(@timestamp >= NOW() - 3 minutes, TO_STRING(error.message), NULL) | STATS verifs = SUM(recent), echecs = SUM(down_recent), dernier_up = MAX(up_ts), erreur = MAX(err), cible = MAX(url.full) BY host.name = noeud, logstash.pipeline.name = pipeline, port, monitor.name, observer.geo.name | EVAL dernier_succes = COALESCE(TO_STRING(dernier_up), \"aucun dans la dernière heure\"), erreur = COALESCE(erreur, \"inconnue\") | WHERE verifs >= 2 AND echecs == verifs | KEEP host.name, logstash.pipeline.name, port, monitor.name, observer.geo.name, cible, verifs, echecs, dernier_succes, erreur"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 60,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{#context.hits}}{{_source.host.name}}{{/context.hits}} : input {{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}} injoignable ({{#context.hits}}{{_source.port}}{{/context.hits}})\",\"raison\":\"{{#context.hits}}{{_source.cible}}{{/context.hits}} ne répond pas depuis l'emplacement {{#context.hits}}{{_source.observer.geo.name}}{{/context.hits}} : {{#context.hits}}{{_source.echecs}}{{/context.hits}}/{{#context.hits}}{{_source.verifs}}{{/context.hits}} vérifications en échec sur 3 min. Erreur : {{#context.hits}}{{_source.erreur}}{{/context.hits}}. Dernier succès : {{#context.hits}}{{_source.dernier_succes}}{{/context.hits}}. Les émetteurs dirigés vers ce nœud perdent leurs données. Vérifier : pipeline RUNNING (health report), systemctl status logstash, ss -ltn sur le nœud.\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P1\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{alert.id}} : rétabli\",\"raison\":\"{{context.message}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### logstash-disk

**[P2/P1] Logstash – disque des nœuds (files persistantes)**

Disque `/` des nœuds Logstash (où sont les files persistantes) : WARNING ≥ 70 %, CRITICAL ≥ 80 %.

| Paramètre | Valeur |
|---|---|
| Type | Custom threshold (`observability.rules.custom_threshold`, consumer `logs`) |
| État dans le lab | **active** |
| Tags | `logstash`, `queue`, `disk`, `elk-lab`, `P2`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Vue de données | `lab-dv-system-filesystem` |
| Filtre KQL | `host.name : logstash-* and system.filesystem.mount_point : "/"` |
| Regroupement | `host.name`, `system.filesystem.mount_point` |
| Alerte si absence de données | false (groupe disparu : false) |
| Agrégations | A = max(`system.filesystem.used.pct`) |
| Équation | `A * 100` (Disque utilisé (%)) |
| Fenêtre | 5 m |
| **WARNING (P2)** | >= 70 |
| **CRITICAL** | >= 80 |

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `custom_threshold.warning` | warning | P2 |
| `custom_threshold.fired` | critical | P1 |
| `recovered` | recovered | P2/P1 |

<details><summary>JSON complet (POST /api/alerting/rule/logstash-disk)</summary>

```json
{
  "name": "[P2/P1] Logstash – disque des nœuds (files persistantes)",
  "rule_type_id": "observability.rules.custom_threshold",
  "consumer": "logs",
  "tags": [
    "logstash",
    "queue",
    "disk",
    "elk-lab",
    "P2",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "criteria": [
      {
        "label": "Disque utilisé (%)",
        "metrics": [
          {
            "name": "A",
            "aggType": "max",
            "field": "system.filesystem.used.pct"
          }
        ],
        "equation": "A * 100",
        "comparator": ">=",
        "threshold": [
          80
        ],
        "warningComparator": ">=",
        "warningThreshold": [
          70
        ],
        "timeSize": 5,
        "timeUnit": "m"
      }
    ],
    "groupBy": [
      "host.name",
      "system.filesystem.mount_point"
    ],
    "alertOnNoData": false,
    "alertOnGroupDisappear": false,
    "searchConfiguration": {
      "index": "lab-dv-system-filesystem",
      "query": {
        "query": "host.name : logstash-* and system.filesystem.mount_point : \"/\"",
        "language": "kuery"
      },
      "filter": []
    }
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "custom_threshold.warning",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"warning\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P2\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "custom_threshold.fired",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P1\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P2/P1\"}"
      }
    }
  ]
}
```

</details>

### logstash-queue-fill-pq

**[P2/P1] Logstash – remplissage file persistante (WARNING 20 % / CRITICAL 50 %)**

Remplissage des files persistantes (disque) : WARNING ≥ 20 %, CRITICAL ≥ 50 %, uniquement quand plus de 1 000 événements sont en attente.

| Paramètre | Valeur |
|---|---|
| Type | Custom threshold (`observability.rules.custom_threshold`, consumer `logs`) |
| État dans le lab | **active** |
| Tags | `logstash`, `queue`, `pq`, `elk-lab`, `P2`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Vue de données | `lab-dv-logstash-pipeline` |
| Filtre KQL | `logstash.pipeline.total.queues.type : "persisted" and logstash.pipeline.total.queues.events > 1000` |
| Regroupement | `host.name`, `logstash.pipeline.name` |
| Alerte si absence de données | false (groupe disparu : false) |
| Agrégations | A = max(`logstash.pipeline.total.queues.current_size.bytes`), B = max(`logstash.pipeline.total.queues.max_size.bytes`) |
| Équation | `A / B * 100` (Remplissage PQ (%) = current_size / max_size) |
| Fenêtre | 2 m |
| **WARNING (P2)** | >= 20 |
| **CRITICAL** | >= 50 |

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `custom_threshold.warning` | warning | P2 |
| `custom_threshold.fired` | critical | P1 |
| `recovered` | recovered | P2/P1 |

<details><summary>JSON complet (POST /api/alerting/rule/logstash-queue-fill-pq)</summary>

```json
{
  "name": "[P2/P1] Logstash – remplissage file persistante (WARNING 20 % / CRITICAL 50 %)",
  "rule_type_id": "observability.rules.custom_threshold",
  "consumer": "logs",
  "tags": [
    "logstash",
    "queue",
    "pq",
    "elk-lab",
    "P2",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "criteria": [
      {
        "label": "Remplissage PQ (%) = current_size / max_size",
        "metrics": [
          {
            "name": "A",
            "aggType": "max",
            "field": "logstash.pipeline.total.queues.current_size.bytes"
          },
          {
            "name": "B",
            "aggType": "max",
            "field": "logstash.pipeline.total.queues.max_size.bytes"
          }
        ],
        "equation": "A / B * 100",
        "comparator": ">=",
        "threshold": [
          50
        ],
        "warningComparator": ">=",
        "warningThreshold": [
          20
        ],
        "timeSize": 2,
        "timeUnit": "m"
      }
    ],
    "groupBy": [
      "host.name",
      "logstash.pipeline.name"
    ],
    "alertOnNoData": false,
    "alertOnGroupDisappear": false,
    "searchConfiguration": {
      "index": "lab-dv-logstash-pipeline",
      "query": {
        "query": "logstash.pipeline.total.queues.type : \"persisted\" and logstash.pipeline.total.queues.events > 1000",
        "language": "kuery"
      },
      "filter": []
    }
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "custom_threshold.warning",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"warning\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P2\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "custom_threshold.fired",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P1\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P2/P1\"}"
      }
    }
  ]
}
```

</details>

### logstash-queue-saturation-memory

**[P2/P1] Logstash – saturation file mémoire (WARNING 0,2 / CRITICAL 0,5 de contre-pression)**

Saturation des files mémoire, mesurée par la contre-pression (Logstash ne publie pas de taux de remplissage pour la file mémoire) : WARNING ≥ 0,2, CRITICAL ≥ 0,5.

| Paramètre | Valeur |
|---|---|
| Type | Custom threshold (`observability.rules.custom_threshold`, consumer `logs`) |
| État dans le lab | **active** |
| Tags | `logstash`, `queue`, `memory`, `elk-lab`, `P2`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Vue de données | `lab-dv-logstash-pipeline` |
| Filtre KQL | `logstash.pipeline.total.queues.type : "memory"` |
| Regroupement | `host.name`, `logstash.pipeline.name` |
| Alerte si absence de données | false (groupe disparu : false) |
| Agrégations | A = avg(`logstash.pipeline.total.flow.queue_backpressure.last_1_minute`) |
| Équation | `A` (Contre-pression file mémoire (inputs bloqués, moyenne 1 min)) |
| Fenêtre | 2 m |
| **WARNING (P2)** | >= 0.2 |
| **CRITICAL** | >= 0.5 |

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `custom_threshold.warning` | warning | P2 |
| `custom_threshold.fired` | critical | P1 |
| `recovered` | recovered | P2/P1 |

<details><summary>JSON complet (POST /api/alerting/rule/logstash-queue-saturation-memory)</summary>

```json
{
  "name": "[P2/P1] Logstash – saturation file mémoire (WARNING 0,2 / CRITICAL 0,5 de contre-pression)",
  "rule_type_id": "observability.rules.custom_threshold",
  "consumer": "logs",
  "tags": [
    "logstash",
    "queue",
    "memory",
    "elk-lab",
    "P2",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "criteria": [
      {
        "label": "Contre-pression file mémoire (inputs bloqués, moyenne 1 min)",
        "metrics": [
          {
            "name": "A",
            "aggType": "avg",
            "field": "logstash.pipeline.total.flow.queue_backpressure.last_1_minute"
          }
        ],
        "equation": "A",
        "comparator": ">=",
        "threshold": [
          0.5
        ],
        "warningComparator": ">=",
        "warningThreshold": [
          0.2
        ],
        "timeSize": 2,
        "timeUnit": "m"
      }
    ],
    "groupBy": [
      "host.name",
      "logstash.pipeline.name"
    ],
    "alertOnNoData": false,
    "alertOnGroupDisappear": false,
    "searchConfiguration": {
      "index": "lab-dv-logstash-pipeline",
      "query": {
        "query": "logstash.pipeline.total.queues.type : \"memory\"",
        "language": "kuery"
      },
      "filter": []
    }
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "custom_threshold.warning",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"warning\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P2\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "custom_threshold.fired",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P1\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P2/P1\"}"
      }
    }
  ]
}
```

</details>

### kb-synth-stale

**[P2] Synthetics – sonde muette (moniteurs des inputs non exécutés)**

Aucune vérification Synthetics reçue d'un emplacement depuis plus de 180 s, par rapport aux métriques système des hôtes Logstash : les ports ne sont plus surveillés.

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | **active** |
| Tags | `logstash`, `synthetics`, `elk-lab`, `P2` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Fenêtre de temps | 60 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM synthetics-*,metrics-system.*
| WHERE host.name LIKE "logstash-*" OR data_stream.type == "synthetics"
| EVAL metric_ts = CASE(data_stream.type == "metrics", @timestamp, NULL)
| INLINE STATS anchor = MAX(metric_ts)
| WHERE data_stream.type == "synthetics" AND summary.up IS NOT NULL
| STATS derniere_verif = MAX(@timestamp), anchor = MAX(anchor) BY observer.geo.name
| EVAL retard_s = DATE_DIFF("second", derniere_verif, anchor)
| WHERE retard_s > 180
| KEEP observer.geo.name, derniere_verif, retard_s
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | warning | P2 |
| `recovered` | recovered | P2 |

<details><summary>JSON complet (POST /api/alerting/rule/kb-synth-stale)</summary>

```json
{
  "name": "[P2] Synthetics – sonde muette (moniteurs des inputs non exécutés)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "synthetics",
    "elk-lab",
    "P2"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM synthetics-*,metrics-system.* | WHERE host.name LIKE \"logstash-*\" OR data_stream.type == \"synthetics\" | EVAL metric_ts = CASE(data_stream.type == \"metrics\", @timestamp, NULL) | INLINE STATS anchor = MAX(metric_ts) | WHERE data_stream.type == \"synthetics\" AND summary.up IS NOT NULL | STATS derniere_verif = MAX(@timestamp), anchor = MAX(anchor) BY observer.geo.name | EVAL retard_s = DATE_DIFF(\"second\", derniere_verif, anchor) | WHERE retard_s > 180 | KEEP observer.geo.name, derniere_verif, retard_s"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 60,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"warning\",\"priorite\":\"P2\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"Emplacement Synthetics {{#context.hits}}{{_source.observer.geo.name}}{{/context.hits}} muet depuis {{#context.hits}}{{_source.retard_s}}{{/context.hits}} s\",\"raison\":\"Aucune vérification Synthetics reçue de l'emplacement {{#context.hits}}{{_source.observer.geo.name}}{{/context.hits}} depuis {{#context.hits}}{{_source.retard_s}}{{/context.hits}} s (dernière : {{#context.hits}}{{_source.derniere_verif}}{{/context.hits}}) : les ports des inputs Logstash ne sont plus surveillés. Vérifier : conteneur synthetics-agent (docker compose ps), état de l'agent dans Fleet.\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"priorite\":\"P2\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{alert.id}} : rétabli\",\"raison\":\"{{context.message}}\",\"date\":\"{{context.date}}\"}"
      }
    }
  ]
}
```

</details>

### logstash-pq-time-to-full

**[P1] Logstash – file persistante pleine dans moins de 30 min**

File persistante pleine dans moins de 30 min au rythme de croissance actuel.

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | désactivée |
| Tags | `logstash`, `queue`, `elk-lab`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Fenêtre de temps | 3 m sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM metrics-logstash.pipeline-*
| WHERE logstash.pipeline.total.queues.type == "persisted"
| STATS used = MAX(logstash.pipeline.total.queues.current_size.bytes), max = MAX(logstash.pipeline.total.queues.max_size.bytes), events = MAX(logstash.pipeline.total.queues.events), growth_events = AVG(logstash.pipeline.total.flow.queue_persisted_growth_events.last_1_minute), growth = AVG(logstash.pipeline.total.flow.queue_persisted_growth_bytes.last_1_minute) BY host.name, logstash.pipeline.name
| WHERE events > 0 AND growth_events > 0 AND growth > 0
| EVAL fill_pct = ROUND(100.0 * used / max, 1), minutes_to_full = ROUND(TO_DOUBLE(max - used) / growth / 60.0, 1)
| WHERE minutes_to_full < 30
| KEEP host.name, logstash.pipeline.name, events, fill_pct, minutes_to_full
| SORT minutes_to_full
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | warning | P1 |
| `recovered` | recovered | P1 |

<details><summary>JSON complet (POST /api/alerting/rule/logstash-pq-time-to-full)</summary>

```json
{
  "name": "[P1] Logstash – file persistante pleine dans moins de 30 min",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "queue",
    "elk-lab",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM metrics-logstash.pipeline-* | WHERE logstash.pipeline.total.queues.type == \"persisted\" | STATS used = MAX(logstash.pipeline.total.queues.current_size.bytes), max = MAX(logstash.pipeline.total.queues.max_size.bytes), events = MAX(logstash.pipeline.total.queues.events), growth_events = AVG(logstash.pipeline.total.flow.queue_persisted_growth_events.last_1_minute), growth = AVG(logstash.pipeline.total.flow.queue_persisted_growth_bytes.last_1_minute) BY host.name, logstash.pipeline.name | WHERE events > 0 AND growth_events > 0 AND growth > 0 | EVAL fill_pct = ROUND(100.0 * used / max, 1), minutes_to_full = ROUND(TO_DOUBLE(max - used) / growth / 60.0, 1) | WHERE minutes_to_full < 30 | KEEP host.name, logstash.pipeline.name, events, fill_pct, minutes_to_full | SORT minutes_to_full"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 3,
    "timeWindowUnit": "m",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 100,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"warning\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\",\"priorite\":\"P1\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"date\":\"{{context.date}}\",\"priorite\":\"P1\"}"
      }
    }
  ]
}
```

</details>

### logstash-pq-fill

**[P2/P1] Logstash – remplissage de la file persistante**

Ancienne règle de remplissage PQ (WARNING 50 %, CRITICAL 80 %), remplacée par `logstash-queue-fill-pq`.

| Paramètre | Valeur |
|---|---|
| Type | Custom threshold (`observability.rules.custom_threshold`, consumer `logs`) |
| État dans le lab | désactivée |
| Tags | `logstash`, `queue`, `elk-lab`, `P2`, `P1` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Vue de données | `lab-dv-logstash-pipeline` |
| Filtre KQL | `logstash.pipeline.total.queues.type : "persisted" and logstash.pipeline.total.queues.events > 0` |
| Regroupement | `host.name`, `logstash.pipeline.name` |
| Alerte si absence de données | false (groupe disparu : false) |
| Agrégations | A = max(`logstash.pipeline.total.queues.current_size.bytes`), B = max(`logstash.pipeline.total.queues.max_size.bytes`) |
| Équation | `A / B * 100` (Remplissage PQ (%)) |
| Fenêtre | 2 m |
| **WARNING (P2)** | >= 50 |
| **CRITICAL** | >= 80 |

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `custom_threshold.warning` | warning | P2 |
| `custom_threshold.fired` | critical | P1 |
| `recovered` | recovered | P2/P1 |

<details><summary>JSON complet (POST /api/alerting/rule/logstash-pq-fill)</summary>

```json
{
  "name": "[P2/P1] Logstash – remplissage de la file persistante",
  "rule_type_id": "observability.rules.custom_threshold",
  "consumer": "logs",
  "tags": [
    "logstash",
    "queue",
    "elk-lab",
    "P2",
    "P1"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "criteria": [
      {
        "label": "Remplissage PQ (%)",
        "metrics": [
          {
            "name": "A",
            "aggType": "max",
            "field": "logstash.pipeline.total.queues.current_size.bytes"
          },
          {
            "name": "B",
            "aggType": "max",
            "field": "logstash.pipeline.total.queues.max_size.bytes"
          }
        ],
        "equation": "A / B * 100",
        "comparator": ">=",
        "threshold": [
          80
        ],
        "warningComparator": ">=",
        "warningThreshold": [
          50
        ],
        "timeSize": 2,
        "timeUnit": "m"
      }
    ],
    "groupBy": [
      "host.name",
      "logstash.pipeline.name"
    ],
    "alertOnNoData": false,
    "alertOnGroupDisappear": false,
    "searchConfiguration": {
      "index": "lab-dv-logstash-pipeline",
      "query": {
        "query": "logstash.pipeline.total.queues.type : \"persisted\" and logstash.pipeline.total.queues.events > 0",
        "language": "kuery"
      },
      "filter": []
    }
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "custom_threshold.warning",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"warning\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P2\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "custom_threshold.fired",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"critical\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P1\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P2/P1\"}"
      }
    }
  ]
}
```

</details>

### logstash-backpressure

**[P2] Logstash – contre-pression prolongée sur un pipeline**

Ancienne règle de contre-pression (> 0,5 sur 5 min), remplacée par `logstash-queue-saturation-memory`.

| Paramètre | Valeur |
|---|---|
| Type | Custom threshold (`observability.rules.custom_threshold`, consumer `logs`) |
| État dans le lab | désactivée |
| Tags | `logstash`, `queue`, `elk-lab`, `P2` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Vue de données | `lab-dv-logstash-pipeline` |
| Filtre KQL | `` |
| Regroupement | `host.name`, `logstash.pipeline.name` |
| Alerte si absence de données | false (groupe disparu : false) |
| Agrégations | A = avg(`logstash.pipeline.total.flow.queue_backpressure.last_1_minute`) |
| Équation | `A` (Contre-pression (queue_backpressure, moyenne 1 min)) |
| Fenêtre | 5 m |
| **CRITICAL** | > 0.5 |

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `custom_threshold.fired` | warning | P2 |
| `recovered` | recovered | P2 |

<details><summary>JSON complet (POST /api/alerting/rule/logstash-backpressure)</summary>

```json
{
  "name": "[P2] Logstash – contre-pression prolongée sur un pipeline",
  "rule_type_id": "observability.rules.custom_threshold",
  "consumer": "logs",
  "tags": [
    "logstash",
    "queue",
    "elk-lab",
    "P2"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "criteria": [
      {
        "label": "Contre-pression (queue_backpressure, moyenne 1 min)",
        "metrics": [
          {
            "name": "A",
            "aggType": "avg",
            "field": "logstash.pipeline.total.flow.queue_backpressure.last_1_minute"
          }
        ],
        "equation": "A",
        "comparator": ">",
        "threshold": [
          0.5
        ],
        "timeSize": 5,
        "timeUnit": "m"
      }
    ],
    "groupBy": [
      "host.name",
      "logstash.pipeline.name"
    ],
    "alertOnNoData": false,
    "alertOnGroupDisappear": false,
    "searchConfiguration": {
      "index": "lab-dv-logstash-pipeline",
      "query": {
        "query": "",
        "language": "kuery"
      },
      "filter": []
    }
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "custom_threshold.fired",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"warning\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"lien\":\"{{context.alertDetailsUrl}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P2\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"groupe\":\"{{alert.id}}\",\"valeur\":\"{{context.value}}\",\"raison\":\"{{context.reason}}\",\"date\":\"{{context.timestamp}}\",\"priorite\":\"P2\"}"
      }
    }
  ]
}
```

</details>

### logstash-metrics-stale

**[P2] Logstash – métriques de pipeline absentes (nœud ou agent muet)**

Métriques de pipeline absentes depuis plus de 3 min (nœud ou agent muet).

| Paramètre | Valeur |
|---|---|
| Type | Elasticsearch query (ES\|QL) (`.es-query`, consumer `stackAlerts`) |
| État dans le lab | désactivée |
| Tags | `logstash`, `queue`, `elk-lab`, `P2` |
| Intervalle | 1m |
| Confirmations avant alerte (`alert_delay.active`) | 2 |
| Fenêtre de temps | 1 h sur `@timestamp` |
| Condition | nombre de lignes > 0, une alerte par ligne (`groupBy: row`) |

Requête ES|QL :

```esql
FROM metrics-logstash.pipeline-*
| STATS last_seen = MAX(@timestamp) BY host.name, logstash.pipeline.name
| WHERE last_seen < NOW() - 3 minutes
| EVAL minutes_silence = DATE_DIFF("minute", last_seen, NOW())
| KEEP host.name, logstash.pipeline.name, last_seen, minutes_silence
| SORT host.name, logstash.pipeline.name
```

Actions (connecteur `lab-webhook-logstash`, notification au changement de groupe d'actions) :

| Groupe d'actions | Sévérité | Priorité |
|---|---|---|
| `query matched` | nodata | P2 |
| `recovered` | recovered | P2 |

<details><summary>JSON complet (POST /api/alerting/rule/logstash-metrics-stale)</summary>

```json
{
  "name": "[P2] Logstash – métriques de pipeline absentes (nœud ou agent muet)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "queue",
    "elk-lab",
    "P2"
  ],
  "schedule": {
    "interval": "1m"
  },
  "alert_delay": {
    "active": 2
  },
  "params": {
    "searchType": "esqlQuery",
    "esqlQuery": {
      "esql": "FROM metrics-logstash.pipeline-* | STATS last_seen = MAX(@timestamp) BY host.name, logstash.pipeline.name | WHERE last_seen < NOW() - 3 minutes | EVAL minutes_silence = DATE_DIFF(\"minute\", last_seen, NOW()) | KEEP host.name, logstash.pipeline.name, last_seen, minutes_silence | SORT host.name, logstash.pipeline.name"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 1,
    "timeWindowUnit": "h",
    "threshold": [
      0
    ],
    "thresholdComparator": ">",
    "size": 500,
    "groupBy": "row"
  },
  "actions": [
    {
      "id": "lab-webhook-logstash",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"nodata\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"lien\":\"{{context.link}}\",\"date\":\"{{context.date}}\",\"priorite\":\"P2\"}"
      }
    },
    {
      "id": "lab-webhook-logstash",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "body": "{\"source\":\"kibana-alerting\",\"severite\":\"recovered\",\"regle\":\"{{rule.name}}\",\"alert_id\":\"{{alert.id}}\",\"titre\":\"{{context.title}}\",\"raison\":\"{{context.message}}\",\"date\":\"{{context.date}}\",\"priorite\":\"P2\"}"
      }
    }
  ]
}
```

</details>

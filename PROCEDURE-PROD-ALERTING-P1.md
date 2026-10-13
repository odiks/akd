# Alerting Logstash – mise en production

Procédure pour reproduire dans un cluster **Elastic 9.5** et un **Grafana 13** l'alerting Logstash du lab.

```text
Elastic Agent (System, Logstash, Osquery, Synthetics) ──► Elasticsearch
        Kibana : 14 règles ──► connecteur Index ──► alerts-elklab (journal : firing / recovered)
        Grafana ──► alerts-elklab, .alerts-*, métriques ──► dashboard « Logstash – pipelines »
```

Chaque règle écrit un document à chaque **déclenchement** (`firing`) et à chaque **levée** (`recovered`) dans
l'index `alerts-elklab`, avec un message lisible ; Grafana affiche ce journal. Tout se colle dans **Kibana Dev
Tools** (une requête `kbn:` part vers l'API Kibana, les autres vers Elasticsearch) et dans Grafana.
**Ordre** : sections 1 à 5, puis vérification (6).

*Généré par `docs/gen_procedure.py` à partir des fichiers déployés dans le lab : ne pas modifier à la main.*

## 0. Prérequis et valeurs à adapter

| Élément | Valeur |
|---|---|
| Versions | Elasticsearch / Kibana 9.5 (ES\|QL `INLINE STATS`, `LOOKUP JOIN`) ; Grafana 13 (ES\|QL dans la datasource Elasticsearch) |
| Kibana | `xpack.encryptedSavedObjects.encryptionKey` défini, **identique sur toutes les instances** (sans elle : ni règle, ni connecteur) |
| Licence | Règles ES\|QL, `LOOKUP JOIN`, connecteur Index : licence de base |
| Droits | Elasticsearch : `manage_security`, `manage_index_templates`, `manage_ilm` ; Kibana : *Rules*, *Connectors*, *Fleet*, *Osquery*, *Synthetics* |
| Nœuds Logstash | Installation `/DATA/logstash`, API `127.0.0.1:9600`. Identifiés par la **balise `logstash.pool`** de leur agent policy (section 3.1), pas par leur nom : sans cette balise, un nœud n'est surveillé par aucune règle nœud |

À adapter dans les blocs de cette procédure :

| Valeur | À remplacer par |
|---|---|
| Tag `elk-lab` | Le tag de votre périmètre (règles ; panels Grafana *qualité des règles*) |
| `<…>` | Mots de passe, policies, ID d'emplacement Synthetics, version de l'intégration System |

## 1. Elasticsearch

### 1.1 Utilisateur de lecture pour Grafana

Lecture seule : données collectées (`metrics-*`, `logs-*`, `synthetics-*`), alertes Kibana (`.alerts-*`), journal
`alerts-elklab*` et journal d'exécution des règles (`.kibana-event-log-*`, panel *santé des règles*). `monitor` sert
au test de santé des datasources.

```
PUT _security/role/grafana_reader
{"cluster": ["monitor"], "indices": [
  {"names": ["metrics-*", "logs-*", "synthetics-*"], "privileges": ["read", "view_index_metadata"]},
  {"names": [".alerts-*", ".internal.alerts-*", "alerts-elklab*", ".kibana-event-log-*"], "privileges": ["read", "view_index_metadata"]}]}

PUT _security/user/grafana_reader
{"password": "<MOT_DE_PASSE_GRAFANA>", "roles": ["grafana_reader"], "full_name": "Grafana - lecture seule"}
```

### 1.2 Journal des alertes `alerts-elklab`

ILM (rotation à 30 jours ou 10 Go, suppression après 1 an), modèle d'index au mapping **strict** (tout champ
imprévu est refusé), premier index et alias d'écriture : les règles écrivent dans l'alias `alerts-elklab`.

| Champ | Contenu |
|---|---|
| `time`, `@timestamp` | Heure de l'événement |
| `status` | `firing` (déclenchement) / `recovered` (levée) |
| `priority`, `severity` | `P1` / `P2` ; `critical` / `warning` (`ok` à la levée) |
| `host`, `pipeline` | Nœud (ou pool, ou liste de nœuds), pipeline concerné |
| `problem` | Message lisible propre à la règle |
| `rule`, `rule_id`, `alert_id` | Règle et identifiant de l'alerte (nœud, pipeline…), réutilisé à chaque nouvelle occurrence |
| `alert_uuid` | Identifiant unique d'une occurrence d'alerte : relie une levée à **son** déclenchement (durée) |

```
PUT _ilm/policy/alerts-elklab
{"policy": {"phases": {"hot": {"actions": {"rollover": {"max_age": "30d", "max_primary_shard_size": "10gb"}}},
                        "delete": {"min_age": "365d", "actions": {"delete": {}}}}}}

PUT _index_template/alerts-elklab
{"index_patterns": ["alerts-elklab-*"], "priority": 500,
 "template": {"settings": {"number_of_shards": 1, "number_of_replicas": 1,
                           "index.lifecycle.name": "alerts-elklab", "index.lifecycle.rollover_alias": "alerts-elklab"},
   "mappings": {"dynamic": "strict", "properties": {
     "@timestamp": {"type": "date"}, "time": {"type": "date"},
     "host": {"type": "keyword"}, "pipeline": {"type": "keyword"}, "severity": {"type": "keyword"},
     "priority": {"type": "keyword"}, "status": {"type": "keyword"},
     "problem": {"type": "text", "fields": {"keyword": {"type": "keyword", "ignore_above": 1024}}},
     "rule": {"type": "keyword"}, "rule_id": {"type": "keyword"}, "alert_id": {"type": "keyword"},
     "alert_uuid": {"type": "keyword"}}}}}

PUT alerts-elklab-000001
{"aliases": {"alerts-elklab": {"is_write_index": true}}}
```

Vérification : `GET alerts-elklab/_mapping` → `"dynamic": "strict"` et 12 champs.

### 1.3 Champ `logstash.pool`

Chaque document collecté porte le pool de son nœud (balise globale de la policy, section 3.1). Les data streams
Logstash ont `dynamic: false` : le champ doit être mappé explicitement, pour les futurs index et les index existants.

```
PUT _component_template/logstash@custom
{"template": {"mappings": {"properties": {"logstash": {"properties": {"pool": {"type": "keyword"}}}}}}}

PUT metrics-logstash.*,logs-logstash.*/_mapping?allow_no_indices=true
{"properties": {"logstash": {"properties": {"pool": {"type": "keyword"}}}}}
```

### 1.4 Inventaire des pipelines critiques

Utilisé par la règle `ls-pipe-throughput` (`LOOKUP JOIN`). Une ligne par pipeline critique : débit minimum attendu
**pour tout le pool** (`min_eps`, événements/s, à fixer d'après le débit habituel le plus bas : nuit, week-end),
priorité de l'alerte, équipe propriétaire. Les valeurs ci-dessous sont celles du lab.

```
PUT logstash-pipelines-critiques
{"settings": {"index.mode": "lookup"}, "mappings": {"properties": {
  "logstash": {"properties": {"pipeline": {"properties": {"name": {"type": "keyword"}}}}},
  "min_eps": {"type": "double"}, "priority": {"type": "keyword"}, "owner": {"type": "keyword"}}}}

POST logstash-pipelines-critiques/_bulk?refresh=true
{"index": {}}
{"logstash": {"pipeline": {"name": "pool1-syslog_in"}}, "min_eps": 50.0, "priority": "P1", "owner": "equipe-logs"}
{"index": {}}
{"logstash": {"pipeline": {"name": "pool1-syslog-es_out"}}, "min_eps": 50.0, "priority": "P1", "owner": "equipe-logs"}
{"index": {}}
{"logstash": {"pipeline": {"name": "pool1-heartbeat_in"}}, "min_eps": 0.02, "priority": "P2", "owner": "equipe-elk"}
{"index": {}}
{"logstash": {"pipeline": {"name": "pool1-elasticsearch_out"}}, "min_eps": 0.02, "priority": "P2", "owner": "equipe-elk"}
```

Modifier un seuil : `POST logstash-pipelines-critiques/_update_by_query` (ou supprimer et recharger l'index).

## 2. Kibana

### 2.1 Connecteur des règles

Toutes les règles écrivent dans le journal par ce connecteur Index (ID `alerts-elklab-index`).

```
POST kbn:/api/actions/connector/alerts-elklab-index
{"connector_type_id": ".index", "name": "Alert log (alerts-elklab)",
 "config": {"index": "alerts-elklab", "refresh": true, "executionTimeField": "@timestamp"}}

POST kbn:/api/actions/connector/alerts-elklab-index/_execute
{"params": {"documents": [{"rule_id": "TEST", "rule": "Connector test", "alert_id": "test", "host": "test", "pipeline": "",
  "severity": "ok", "priority": "P1", "status": "recovered", "problem": "Write test", "time": "2026-01-01T00:00:00Z"}]}}

POST alerts-elklab/_delete_by_query?refresh=true
{"query": {"term": {"rule_id": "TEST"}}}
```

Le `_execute` doit répondre `"status": "ok"`.

### 2.2 Vue de données de la règle disque

La règle *custom threshold* `logstash-disk` lit les données par une vue de données ; son `id` est référencé par la règle.

```
POST kbn:/api/data_views/data_view
{"data_view": {"id": "lab-dv-system-filesystem", "name": "System – filesystems", "title": "metrics-system.filesystem-*", "timeFieldName": "@timestamp"}, "override": true}
```

## 3. Fleet : collecte

### 3.1 Agent policies et intégrations

**Une agent policy par pool** de Logstash, avec la balise globale `logstash.pool` = nom du pool
(*Fleet → Agent policies → <policy> → Settings → Custom fields*). **Obligatoire** : les règles nœud (*process not running*,
*pool down*, *monitoring data missing*, disque) ne surveillent que les hôtes qui portent cette balise (`logstash.pool IS NOT NULL`). Intégrations de chaque policy :

| Intégration | Réglage | Utilisée par |
|---|---|---|
| System (instance par défaut) | Par défaut | `logstash-disk`, *pool down* |
| System (**instance dédiée**) | Process Logstash seul, ci-dessous | *process not running*, panels process |
| Logstash | Logs `/DATA/logstash/logs/logstash-plain*.log` ; métriques `http://127.0.0.1:9600` (node, pipeline, health report) | Règles logs, pipelines, files |
| Osquery Manager | **Une intégration par policy** (section 3.2) | *input port closed* |

**Instance System dédiée au process Logstash.** L'instance par défaut ne collecte que les 5 process les plus
gourmands : Logstash peut en être absent, et *process not running* déclencherait de fausses P1. Celle-ci ne
collecte que le process `java` de Logstash (filtre sur `org.logstash.Logstash`), toutes les 10 s, dans le
namespace `logstash`. Une par policy de pool (version : `GET kbn:/api/fleet/epm/packages/system`) :

```
POST kbn:/api/fleet/package_policies?format=simplified
{
  "name": "logstash-process-<POOL>",
  "namespace": "logstash",
  "policy_ids": ["<POLICY_POOL>"],
  "package": {"name": "system", "version": "<VERSION_SYSTEM>"},
  "inputs": {"system-logfile": {"enabled": false}, "system-journald": {"enabled": false}, "system-winlog": {"enabled": false}, "system-system/metrics": {"enabled": true, "streams": {"system.process": {"enabled": true, "vars": {"period": "10s", "process.include_top_n.by_cpu": 0, "process.include_top_n.by_memory": 0, "process.cmdline.cache.enabled": true, "processes": ["java"], "processors": "- drop_event:\n    when:\n      not:\n        contains:\n          process.command_line: \"org.logstash.Logstash\"\n"}}, "system.core": {"enabled": false}, "system.cpu": {"enabled": false}, "system.diskio": {"enabled": false}, "system.filesystem": {"enabled": false}, "system.fsstat": {"enabled": false}, "system.load": {"enabled": false}, "system.memory": {"enabled": false}, "system.network": {"enabled": false}, "system.ntp": {"enabled": false}, "system.process.summary": {"enabled": false}, "system.socket_summary": {"enabled": false}, "system.uptime": {"enabled": false}}}}
}
```

Vérification (après 1 min) :

```
POST _query?format=txt
{"query": "FROM metrics-system.process-logstash | STATS latest = MAX(@timestamp) BY host.name, logstash.pool"}
```

### 3.2 Osquery : ports d'input par pool

Kibana injecte un pack dans l'intégration Osquery des policies sélectionnées : si une intégration est **partagée**
entre plusieurs policies, le pack tourne sur tous leurs agents. D'où **une intégration Osquery Manager par
policy** (*Agent policy → Add integration → Osquery Manager*), puis **un pack par pool**.

La requête contient la liste des ports d'input attendus du pool (`VALUES`) et renvoie **une ligne par port** :
`listening` = 1 si le process Logstash écoute sur ce port (hors boucle locale), 0 sinon ; `logstash_running` =
nombre de process Logstash ; `expected_pool` = pool de la requête. Requête du pool1 :

```sql
WITH expected(pipeline, transport, port) AS (VALUES
  ('pool1-syslog_in', 'tcp', 5514),
  ('pool1-syslog_in', 'udp', 5514)
),
open_ports AS (
  SELECT lp.port AS port, CASE lp.protocol WHEN 6 THEN 'tcp' WHEN 17 THEN 'udp' END AS transport
  FROM listening_ports lp JOIN processes p USING (pid)
  WHERE p.cmdline LIKE '%org.logstash.Logstash%' AND lp.address NOT IN ('127.0.0.1', '::1', '::ffff:127.0.0.1')
)
SELECT 'pool1' AS expected_pool, e.pipeline AS pipeline, e.transport AS transport, e.port AS port,
       CASE WHEN EXISTS (SELECT 1 FROM open_ports o WHERE o.port = e.port AND o.transport = e.transport) THEN 1 ELSE 0 END AS listening,
       (SELECT COUNT(*) FROM processes WHERE cmdline LIKE '%org.logstash.Logstash%') AS logstash_running
FROM expected e;
```

```
POST kbn:/api/osquery/packs
{
  "name": "logstash-ports-pool1",
  "enabled": true,
  "policy_ids": ["<POLICY_POOL1>"],
  "queries": {"logstash_ports_pool1": {"query": "WITH expected(pipeline, transport, port) AS (VALUES\n  ('pool1-syslog_in', 'tcp', 5514),\n  ('pool1-syslog_in', 'udp', 5514)\n),\nopen_ports AS (\n  SELECT lp.port AS port, CASE lp.protocol WHEN 6 THEN 'tcp' WHEN 17 THEN 'udp' END AS transport\n  FROM listening_ports lp JOIN processes p USING (pid)\n  WHERE p.cmdline LIKE '%org.logstash.Logstash%' AND lp.address NOT IN ('127.0.0.1', '::1', '::ffff:127.0.0.1')\n)\nSELECT 'pool1' AS expected_pool, e.pipeline AS pipeline, e.transport AS transport, e.port AS port,\n       CASE WHEN EXISTS (SELECT 1 FROM open_ports o WHERE o.port = e.port AND o.transport = e.transport) THEN 1 ELSE 0 END AS listening,\n       (SELECT COUNT(*) FROM processes WHERE cmdline LIKE '%org.logstash.Logstash%') AS logstash_running\nFROM expected e;", "interval": 120, "snapshot": true, "removed": false, "ecs_mapping": {"logstash.pipeline.name": {"field": "pipeline"}, "network.transport": {"field": "transport"}, "server.port": {"field": "port"}}}}
}
```

- **Autre pool** : même requête avec ses ports, `'<pool>' AS expected_pool`, pack `logstash-ports-<pool>`, requête
  `logstash_ports_<pool>`, policy du pool.
- **Nouveau pipeline** : ajouter sa ligne `('<pipeline>', 'tcp'|'udp', <port>)` dans `VALUES`, puis mettre le pack à
  jour (*Osquery → Packs*, ou `PUT kbn:/api/osquery/packs/<id>`).

Vérification (après 2 à 4 min) :

```
POST _query?format=txt
{"query": "FROM logs-osquery_manager.result-* | WHERE query_name LIKE \"logstash_ports_*\" AND @timestamp > NOW() - 5 minutes | STATS n = COUNT(*), closed = SUM(CASE(TO_LONG(osquery.listening) == 0, 1, 0)) BY host.name, query_name"}
```

Chaque nœud ne doit apparaître qu'avec la requête de **son** pool, et `closed` = 0.

### 3.3 Synthetics : ping des nœuds

Un emplacement privé (agent `elastic-agent-complete` relié à sa propre policy), puis **un moniteur ICMP par nœud**.
Le nom `ping · <nœud>` est **obligatoire** (la règle en extrait le nœud) ; tag `ping`. Désactiver les règles par
défaut de Synthetics (*Synthetics → Settings → Alerting*) pour éviter les doublons.

```
POST kbn:/api/synthetics/private_locations
{"label": "<NOM_EMPLACEMENT>", "agentPolicyId": "<POLICY_SYNTHETICS>"}

POST kbn:/api/synthetics/monitors
{"type": "icmp", "name": "ping · logstash-1", "host": "logstash-1.<domaine>", "schedule": 1, "wait": "1", "timeout": "10",
 "ipv4": true, "ipv6": false, "tags": ["ping", "pool1", "logstash-1"], "enabled": true, "private_locations": ["<ID_EMPLACEMENT>"]}
```

## 4. Règles d'alerte

### 4.1 Principes

- **Une alerte par ligne** de résultat (`groupBy: row`) : son identifiant est formé des colonnes du dernier
  `STATS … BY` (colonne *Une alerte par* ci-dessous).
- **`alert_delay`** : l'alerte n'est créée qu'après N exécutions consécutives en anomalie (évite les faux positifs ponctuels).
- **Actions** : un document `firing` (groupe `query matched`, ou `custom_threshold.fired` / `.warning`) et un
  document `recovered`, une seule fois par alerte (`notify_when: onActionGroupChange`).
- **Priorité** : `[P1]` fixe ; `[P1/P2]` calculée par la requête (colonne `priority`, qui fait partie de
  l'identifiant : une escalade P2 → P1 lève l'alerte P2 et ouvre une alerte P1) ; `[P2/P1]` : WARNING = P2, CRITICAL = P1.
- Toutes les règles s'exécutent **chaque minute**.

| ID | Priorité | Règle | Type | Fenêtre | Confirmations | Une alerte par |
|---|---|---|---|---|---|---|
| `kb-ls-p1-process-absent` | P1 | Logstash – process not running | ES\|QL | 60 min | 2 | `host.name` |
| `kb-ls-p1-restart-loop` | P1 | Logstash – restart loop | ES\|QL | 10 min | 1 | `host.name` |
| `kb-ls-p1-heap` | P1 | Logstash – critical JVM heap (> 95 % over 10 min) | ES\|QL | 10 min | 1 | `host.name` |
| `kb-ls-p1-oom` | P1 | Logstash – OutOfMemoryError | ES\|QL | 5 min | 1 | `host.name` |
| `kb-ls-p1-pool-down` | P1 | Logstash – pool with no healthy node | ES\|QL | 60 min | 2 | `logstash.pool` |
| `kb-ls-p1-es-unreachable` | P1 | Logstash – Elasticsearch unreachable | ES\|QL | 2 min | 2 | `host.name` |
| `kb-synth-p1-ping-down` | P1 | Logstash – node unreachable (ping) | ES\|QL | 60 min | 2 | `host.name` + `monitor.name` + `observer.geo.name` |
| `logstash-disk` | P2/P1 | Logstash – node disk usage (/ and /DATA) | Custom threshold | 5 min | 2 | `host.name` + `system.filesystem.mount_point` |
| `ls-node-metrics-missing` | P2 | Logstash – monitoring data missing (node) | ES\|QL | 1 h | 3 | `host.name` |
| `ls-pipe-not-running` | P1 | Logstash pipeline – not running | ES\|QL | 10 min | 2 | `host.name` + `logstash.pipeline.name` |
| `ls-pipe-input-port` | P1/P2 | Logstash pipeline – input port closed (Osquery) | ES\|QL | 6 min | 3 | `logstash.pool` + `logstash.pipeline.name` + `port` + `priority` |
| `ls-pipe-queue-risk` | P1/P2 | Logstash pipeline – queue at risk | ES\|QL | 5 min | 2 | `host.name` + `logstash.pipeline.name` + `priority` |
| `kb-ls-p1-index-rejected` | P1 | Logstash pipeline – data loss (events rejected by Elasticsearch) | ES\|QL | 5 min | 1 | `host.name` + `logstash.pipeline.name` |
| `ls-pipe-throughput` | P1/P2 | Logstash pipeline – critical pipeline throughput | ES\|QL | 6 h | 3 | `logstash.pool` + `logstash.pipeline.name` |

### 4.2 Règles nœud et pipeline

#### `kb-ls-p1-process-absent` – [P1] Logstash – process not running

Dernier relevé du process `org.logstash.Logstash` par nœud, comparé à la donnée la plus récente de **tous** les nœuds Logstash (et non à l'heure courante) : un retard global d'ingestion ne déclenche rien. Alerte si l'écart dépasse 120 s.

Source : `metrics-system.*` (instance process dédiée) · fenêtre 60 min · confirmée après 2 exécution(s) · une alerte par `host.name`.
Message : *Logstash process not running for 180 s (last seen: 2026-10-08T19:59:30Z). Check: sudo startlogstash status, …*

```
POST kbn:/api/alerting/rule/kb-ls-p1-process-absent
{
  "name": "[P1] Logstash – process not running",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "elk-lab", "P1"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 2},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM metrics-system.* | WHERE logstash.pool IS NOT NULL | INLINE STATS anchor = MAX(@timestamp) | WHERE data_stream.dataset == \"system.process\" | EVAL ls_ts = CASE(process.command_line LIKE \"*org.logstash.Logstash*\", @timestamp, NULL) | STATS last_ls = MAX(ls_ts), anchor = MAX(anchor) BY host.name | EVAL delay_s = DATE_DIFF(\"second\", COALESCE(last_ls, NOW() - 1 hour), anchor) | WHERE delay_s > 120 | EVAL last_seen_at = COALESCE(TO_STRING(last_ls), \"none in the last hour\") | KEEP host.name, last_ls, last_seen_at, delay_s"}, "timeField": "@timestamp", "timeWindowSize": 60, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-process-absent", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}", "pipeline": "", "severity": "critical", "priority": "P1", "status": "firing", "problem": "Logstash process not running for {{#context.hits}}{{_source.delay_s}}{{/context.hits}} s (last seen: {{#context.hits}}{{_source.last_seen_at}}{{/context.hits}}). Check: sudo startlogstash status, /DATA/logstash/logs/logstash-plain.log", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-process-absent", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{alert.id}}", "pipeline": "", "severity": "ok", "priority": "P1", "status": "recovered", "problem": "Logstash process running again", "time": "{{date}}"}]}}
  ]
}
```

#### `kb-ls-p1-restart-loop` – [P1] Logstash – restart loop

Compte les lignes « Starting Logstash » par nœud sur 10 min ; plus de 2 = crash ou redémarrage en boucle.

Source : `logs-logstash.log-*` · fenêtre 10 min · confirmée après 1 exécution(s) · une alerte par `host.name`.
Message : *3 Logstash starts in 10 min: crash or restart loop. See the beginning of /DATA/logstash/logs/logstash-plain.log …*

```
POST kbn:/api/alerting/rule/kb-ls-p1-restart-loop
{
  "name": "[P1] Logstash – restart loop",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "elk-lab", "P1"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 1},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM logs-logstash.log-* | WHERE data_stream.dataset == \"logstash.log\" AND QSTR(\"message:\\\"Starting Logstash\\\"\") | STATS starts = COUNT(*) BY host.name | WHERE starts > 2"}, "timeField": "@timestamp", "timeWindowSize": 10, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-restart-loop", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}", "pipeline": "", "severity": "critical", "priority": "P1", "status": "firing", "problem": "{{#context.hits}}{{_source.starts}}{{/context.hits}} Logstash starts in 10 min: crash or restart loop. See the beginning of /DATA/logstash/logs/logstash-plain.log and logstash-stdout.log", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-restart-loop", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{alert.id}}", "pipeline": "", "severity": "ok", "priority": "P1", "status": "recovered", "problem": "No more restart loop (fewer than 3 starts in 10 min)", "time": "{{date}}"}]}}
  ]
}
```

#### `kb-ls-p1-heap` – [P1] Logstash – critical JVM heap (> 95 % over 10 min)

Heap JVM moyen par nœud sur 10 min ; au-delà de 95 %, le GC tourne en permanence et l'`OutOfMemoryError` approche.

Source : `metrics-logstash.node-*` · fenêtre 10 min · confirmée après 1 exécution(s) · une alerte par `host.name`.
Message : *JVM heap at 97 % on average over 10 min: constant GC, OutOfMemoryError likely. …*

```
POST kbn:/api/alerting/rule/kb-ls-p1-heap
{
  "name": "[P1] Logstash – critical JVM heap (> 95 % over 10 min)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "elk-lab", "P1"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 1},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM metrics-logstash.node-* | STATS heap_pct = ROUND(AVG(logstash.node.stats.jvm.mem.heap_used_percent), 1) BY host.name | WHERE heap_pct > 95"}, "timeField": "@timestamp", "timeWindowSize": 10, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-heap", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}", "pipeline": "", "severity": "critical", "priority": "P1", "status": "firing", "problem": "JVM heap at {{#context.hits}}{{_source.heap_pct}}{{/context.hits}} % on average over 10 min: constant GC, OutOfMemoryError likely. Increase the heap (config/jvm.options) or reduce the load.", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-heap", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{alert.id}}", "pipeline": "", "severity": "ok", "priority": "P1", "status": "recovered", "problem": "JVM heap back below 95 %", "time": "{{date}}"}]}}
  ]
}
```

#### `kb-ls-p1-oom` – [P1] Logstash – OutOfMemoryError

Toute ligne `OutOfMemoryError` des 5 dernières minutes : le process est instable ou va s'arrêter.

Source : `logs-logstash.log-*` · fenêtre 5 min · confirmée après 1 exécution(s) · une alerte par `host.name`.
Message : *OutOfMemoryError in the Logstash logs (1 occurrence(s) in 5 min): the process is probably unstable or stopped. …*

```
POST kbn:/api/alerting/rule/kb-ls-p1-oom
{
  "name": "[P1] Logstash – OutOfMemoryError",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "elk-lab", "P1"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 1},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM logs-logstash.log-* | WHERE data_stream.dataset == \"logstash.log\" AND QSTR(\"message:\\\"java.lang.OutOfMemoryError\\\" OR message:\\\"OutOfMemoryError\\\"\") | STATS occurrences = COUNT(*) BY host.name"}, "timeField": "@timestamp", "timeWindowSize": 5, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-oom", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}", "pipeline": "", "severity": "critical", "priority": "P1", "status": "firing", "problem": "OutOfMemoryError in the Logstash logs ({{#context.hits}}{{_source.occurrences}}{{/context.hits}} occurrence(s) in 5 min): the process is probably unstable or stopped. Check sudo startlogstash status.", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-oom", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{alert.id}}", "pipeline": "", "severity": "ok", "priority": "P1", "status": "recovered", "problem": "No more OutOfMemoryError in the logs", "time": "{{date}}"}]}}
  ]
}
```

#### `kb-ls-p1-pool-down` – [P1] Logstash – pool with no healthy node

Dernière métrique de pipeline par **pool**, comparée à la donnée la plus récente des nœuds Logstash ; alerte si aucun nœud du pool n'a rien envoyé depuis 180 s : tous les flux du pool sont coupés.

Source : `metrics-logstash.pipeline-*`, `metrics-system.*` · fenêtre 60 min · confirmée après 2 exécution(s) · une alerte par `logstash.pool`.
Message : *No node of pool pool1 has reported pipeline metrics for 392 s (…): all flows of the pool are interrupted.*

```
POST kbn:/api/alerting/rule/kb-ls-p1-pool-down
{
  "name": "[P1] Logstash – pool with no healthy node",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "elk-lab", "P1"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 2},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM metrics-logstash.pipeline-*,metrics-system.* | WHERE logstash.pool IS NOT NULL | INLINE STATS anchor = MAX(@timestamp) | WHERE data_stream.dataset == \"logstash.pipeline\" | STATS last_seen = MAX(@timestamp), anchor = MAX(anchor) BY logstash.pool | EVAL delay_s = DATE_DIFF(\"second\", last_seen, anchor) | WHERE delay_s > 180 | KEEP logstash.pool, last_seen, delay_s"}, "timeField": "@timestamp", "timeWindowSize": 60, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-pool-down", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.logstash.pool}}{{/context.hits}}", "pipeline": "", "severity": "critical", "priority": "P1", "status": "firing", "problem": "No node of pool {{#context.hits}}{{_source.logstash.pool}}{{/context.hits}} has reported pipeline metrics for {{#context.hits}}{{_source.delay_s}}{{/context.hits}} s (last: {{#context.hits}}{{_source.last_seen}}{{/context.hits}}): all flows of the pool are interrupted.", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-pool-down", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{alert.id}}", "pipeline": "", "severity": "ok", "priority": "P1", "status": "recovered", "problem": "The pool reports metrics again", "time": "{{date}}"}]}}
  ]
}
```

#### `kb-ls-p1-es-unreachable` – [P1] Logstash – Elasticsearch unreachable

« Elasticsearch Unreachable » ou « Marking url as dead » dans les 2 dernières minutes : les files vont se remplir, puis les inputs se bloquer.

Source : `logs-logstash.log-*` · fenêtre 2 min · confirmée après 2 exécution(s) · une alerte par `host.name`.
Message : *Elasticsearch unreachable from Logstash: 3 error(s) in 2 min (…). Persistent queues will fill up, then inputs will block.*

```
POST kbn:/api/alerting/rule/kb-ls-p1-es-unreachable
{
  "name": "[P1] Logstash – Elasticsearch unreachable",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "elk-lab", "P1"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 2},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM logs-logstash.log-* | WHERE data_stream.dataset == \"logstash.log\" AND QSTR(\"message:\\\"Elasticsearch Unreachable\\\" OR message:\\\"Marking url as dead\\\"\") | STATS errors = COUNT(*) BY host.name"}, "timeField": "@timestamp", "timeWindowSize": 2, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-es-unreachable", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}", "pipeline": "", "severity": "critical", "priority": "P1", "status": "firing", "problem": "Elasticsearch unreachable from Logstash: {{#context.hits}}{{_source.errors}}{{/context.hits}} error(s) in 2 min (Elasticsearch Unreachable / Marking url as dead). Persistent queues will fill up, then inputs will block.", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-es-unreachable", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{alert.id}}", "pipeline": "", "severity": "ok", "priority": "P1", "status": "recovered", "problem": "Logstash reaches Elasticsearch again", "time": "{{date}}"}]}}
  ]
}
```

#### `kb-synth-p1-ping-down` – [P1] Logstash – node unreachable (ping)

Moniteurs ICMP `ping · <nœud>` : toutes les vérifications des 3 dernières minutes en échec (au moins 2). Le nom du nœud est extrait du nom du moniteur.

Source : `synthetics-icmp-*` · fenêtre 60 min · confirmée après 2 exécution(s) · une alerte par `host.name` + `monitor.name` + `observer.geo.name`.
Message : *The node no longer answers ping from location <emplacement>: 3/3 failures over 3 min (ping timeout). Last success: …*

```
POST kbn:/api/alerting/rule/kb-synth-p1-ping-down
{
  "name": "[P1] Logstash – node unreachable (ping)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "synthetics", "ping", "P1", "elk-lab"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 2},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM synthetics-icmp-* | WHERE summary.up IS NOT NULL AND QSTR(\"tags:ping\") | DISSECT monitor.name \"ping · %{node}\" | EVAL recent = CASE(@timestamp >= NOW() - 3 minutes, 1, 0), down_recent = CASE(@timestamp >= NOW() - 3 minutes AND monitor.status == \"down\", 1, 0), up_ts = CASE(monitor.status == \"up\", @timestamp, NULL), err = CASE(@timestamp >= NOW() - 3 minutes, TO_STRING(error.message), NULL) | STATS checks = SUM(recent), failures = SUM(down_recent), last_up = MAX(up_ts), last_error = MAX(err), ip = MAX(monitor.ip) BY host.name = node, monitor.name, observer.geo.name | EVAL last_success = COALESCE(TO_STRING(last_up), \"none in the last hour\"), last_error = COALESCE(last_error, \"unknown\") | WHERE checks >= 2 AND failures == checks | KEEP host.name, monitor.name, observer.geo.name, checks, failures, last_success, last_error"}, "timeField": "@timestamp", "timeWindowSize": 60, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-synth-p1-ping-down", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}", "pipeline": "", "severity": "critical", "priority": "P1", "status": "firing", "problem": "The node no longer answers ping from location {{#context.hits}}{{_source.observer.geo.name}}{{/context.hits}}: {{#context.hits}}{{_source.failures}}{{/context.hits}}/{{#context.hits}}{{_source.checks}}{{/context.hits}} failures over 3 min ({{#context.hits}}{{_source.last_error}}{{/context.hits}}). Last success: {{#context.hits}}{{_source.last_success}}{{/context.hits}}.", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-synth-p1-ping-down", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "", "pipeline": "", "severity": "ok", "priority": "P1", "status": "recovered", "problem": "The node answers ping again", "time": "{{date}}"}]}}
  ]
}
```

#### `logstash-disk` – [P2/P1] Logstash – node disk usage (/ and /DATA)

Règle *custom threshold* : taux d'occupation maximal de `/` et `/DATA` sur 5 min, par nœud et point de montage. WARNING ≥ 70 % → **P2**, CRITICAL ≥ 80 % → **P1** (files persistantes et logs ne pourraient plus écrire).

Source : `metrics-system.filesystem-*` (vue de données) · fenêtre 5 min · confirmée après 2 exécution(s) · une alerte par `host.name` + `system.filesystem.mount_point`.
Message : *Disk /DATA filled to 90% (CRITICAL threshold 80 %): Logstash persistent queues and logs may no longer be able to write.*

```
POST kbn:/api/alerting/rule/logstash-disk
{
  "name": "[P2/P1] Logstash – node disk usage (/ and /DATA)",
  "rule_type_id": "observability.rules.custom_threshold",
  "consumer": "logs",
  "tags": ["logstash", "queue", "disk", "elk-lab", "P2", "P1"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 2},
  "params": {"criteria": [{"label": "Disk used – / and /DATA (ratio, shown as %)", "metrics": [{"name": "A", "aggType": "max", "field": "system.filesystem.used.pct"}], "equation": "A", "comparator": ">=", "threshold": [0.8], "warningComparator": ">=", "warningThreshold": [0.7], "timeSize": 5, "timeUnit": "m"}], "groupBy": ["host.name", "system.filesystem.mount_point"], "alertOnNoData": false, "alertOnGroupDisappear": false, "searchConfiguration": {"index": "lab-dv-system-filesystem", "query": {"query": "logstash.pool : * and system.filesystem.mount_point : (\"/\" or \"/DATA\")", "language": "kuery"}, "filter": []}},
  "actions": [
    {"id": "alerts-elklab-index", "group": "custom_threshold.warning", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "logstash-disk", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{context.group.0.value}}", "pipeline": "", "severity": "warning", "priority": "P2", "status": "firing", "problem": "Disk {{context.group.1.value}} filled to {{context.value}} (WARNING threshold 70 %).", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "custom_threshold.fired", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "logstash-disk", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{context.group.0.value}}", "pipeline": "", "severity": "critical", "priority": "P1", "status": "firing", "problem": "Disk {{context.group.1.value}} filled to {{context.value}} (CRITICAL threshold 80 %): Logstash persistent queues and logs may no longer be able to write.", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "logstash-disk", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{context.group.0.value}}", "pipeline": "", "severity": "ok", "priority": "P2/P1", "status": "recovered", "problem": "Disk usage back below 70 %", "time": "{{date}}"}]}}
  ]
}
```

#### `ls-node-metrics-missing` – [P2] Logstash – monitoring data missing (node)

Plus aucune métrique de pipeline d'un nœud depuis plus de 3 min (nœud, Logstash ou Elastic Agent muet). Une alerte **par nœud** ; complète *pool down*, qui ne voit que la panne de tout un pool.

Source : `metrics-logstash.pipeline-*` · fenêtre 1 h · confirmée après 3 exécution(s) · une alerte par `host.name`.
Message : *No pipeline metrics from this node for 7 min (last: …, 4 pipeline(s)): node, Logstash or Elastic Agent silent.*

```
POST kbn:/api/alerting/rule/ls-node-metrics-missing
{
  "name": "[P2] Logstash – monitoring data missing (node)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "elk-lab", "P2"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 3},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM metrics-logstash.pipeline-* | WHERE logstash.pool IS NOT NULL | STATS last_seen = MAX(@timestamp), pipelines = COUNT_DISTINCT(logstash.pipeline.name) BY host.name | WHERE last_seen < NOW() - 3 minutes | EVAL silent_minutes = DATE_DIFF(\"minute\", last_seen, NOW()) | KEEP host.name, last_seen, silent_minutes, pipelines"}, "timeField": "@timestamp", "timeWindowSize": 1, "timeWindowUnit": "h", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-node-metrics-missing", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}", "pipeline": "", "severity": "warning", "priority": "P2", "status": "firing", "problem": "No pipeline metrics from this node for {{#context.hits}}{{_source.silent_minutes}}{{/context.hits}} min (last: {{#context.hits}}{{_source.last_seen}}{{/context.hits}}, {{#context.hits}}{{_source.pipelines}}{{/context.hits}} pipeline(s)): node, Logstash or Elastic Agent silent.", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-node-metrics-missing", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{alert.id}}", "pipeline": "", "severity": "ok", "priority": "P2", "status": "recovered", "problem": "Node pipeline metrics received again", "time": "{{date}}"}]}}
  ]
}
```

#### `ls-pipe-not-running` – [P1] Logstash pipeline – not running

Ne garde que le **dernier** health report de chaque pipeline (de moins de 3 min) : alerte s'il est `FINISHED`, `TERMINATED`, `LOADING` ou `red`, **ou** si les logs des 5 dernières minutes contiennent « Address already in use » ou « shutdown process appears to be stalled ». La cause est indiquée dans le message. Les relevés `LOADING` d'un démarrage normal ne déclenchent rien.

Source : `metrics-logstash.health_report-*`, `logs-logstash.log-*` · fenêtre 10 min · confirmée après 2 exécution(s) · une alerte par `host.name` + `logstash.pipeline.name`.
Message : *Pipeline not running: state RUNNING/red, cause: health report (0 error(s) in the logs over 5 min, last health report: …).*

```
POST kbn:/api/alerting/rule/ls-pipe-not-running
{
  "name": "[P1] Logstash pipeline – not running",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "pipeline", "elk-lab", "P1"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 2},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM metrics-logstash.health_report-*, logs-logstash.log-* | WHERE logstash.pipeline.id IS NOT NULL OR (data_stream.dataset == \"logstash.log\" AND (message LIKE \"*Address already in use*\" OR message LIKE \"*shutdown process appears to be stalled*\")) | EVAL node = COALESCE(logstash.node.name, host.name), pipeline = COALESCE(logstash.pipeline.id, logstash.log.pipeline_id, \"(all pipelines)\"), report_ts = CASE(logstash.pipeline.id IS NOT NULL, @timestamp, NULL) | INLINE STATS latest_report = MAX(report_ts) BY node, pipeline | WHERE report_ts IS NULL OR report_ts == latest_report | EVAL report_bad = CASE(report_ts IS NOT NULL AND report_ts > NOW() - 3 minutes AND (logstash.pipeline.state IN (\"FINISHED\", \"TERMINATED\", \"LOADING\") OR logstash.pipeline.status == \"red\"), 1, 0), report_state = CASE(report_ts IS NOT NULL, CONCAT(logstash.pipeline.state, \"/\", logstash.pipeline.status), NULL), log_error = CASE(report_ts IS NULL AND @timestamp > NOW() - 5 minutes, CASE(message LIKE \"*Address already in use*\", \"Address already in use\", \"shutdown stalled\"), NULL), log_hit = CASE(log_error IS NOT NULL, 1, 0) | STATS report_bad = MAX(report_bad), state = MAX(report_state), log_errors = SUM(log_hit), causes = VALUES(log_error), latest_report = MAX(latest_report) BY host.name = node, logstash.pipeline.name = pipeline | WHERE report_bad == 1 OR log_errors > 0 | EVAL state = COALESCE(state, \"no health report\"), cause = COALESCE(MV_CONCAT(causes, \", \"), \"health report\"), latest_report = COALESCE(TO_STRING(latest_report), \"none\") | KEEP host.name, logstash.pipeline.name, state, cause, log_errors, latest_report"}, "timeField": "@timestamp", "timeWindowSize": 10, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-pipe-not-running", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}", "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}", "severity": "critical", "priority": "P1", "status": "firing", "problem": "Pipeline not running: state {{#context.hits}}{{_source.state}}{{/context.hits}}, cause: {{#context.hits}}{{_source.cause}}{{/context.hits}} ({{#context.hits}}{{_source.log_errors}}{{/context.hits}} error(s) in the logs over 5 min, last health report: {{#context.hits}}{{_source.latest_report}}{{/context.hits}}). Check /DATA/logstash/logs/logstash-plain.log", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-pipe-not-running", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "", "pipeline": "", "severity": "ok", "priority": "P1", "status": "recovered", "problem": "Pipeline running again", "time": "{{date}}"}]}}
  ]
}
```

#### `ls-pipe-input-port` – [P1/P2] Logstash pipeline – input port closed (Osquery)

Dernier relevé Osquery de chaque nœud où Logstash tourne : une ligne par port attendu, `listening` 1/0. Compte les nœuds où le port est fermé : **P1** si c'est tout le pool (flux coupé), **P2** si c'est une partie. Ne garde que les lignes dont `expected_pool` = `logstash.pool` du nœud.

Source : `logs-osquery_manager.result-*` (packs `logstash_ports_<pool>`) · fenêtre 6 min · confirmée après 3 exécution(s) · une alerte par `logstash.pool` + `logstash.pipeline.name` + `port` + `priority`.
Message : *Input port tcp/5999 closed on 2/2 node(s) of pool pool1 (logstash-1, logstash-2), the whole pool: the flow is cut. …*

```
POST kbn:/api/alerting/rule/ls-pipe-input-port
{
  "name": "[P1/P2] Logstash pipeline – input port closed (Osquery)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "pipeline", "elk-lab", "P1", "P2", "osquery"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 3},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM logs-osquery_manager.result-* | WHERE query_name LIKE \"logstash_ports_*\" AND osquery.expected_pool IS NOT NULL AND osquery.expected_pool == logstash.pool | INLINE STATS latest_run = MAX(osquery_meta.planned_schedule_time) BY host.name | WHERE osquery_meta.planned_schedule_time == latest_run AND osquery.logstash_running > 0 | EVAL port = CONCAT(network.transport, \"/\", TO_STRING(server.port)), closed = CASE(TO_LONG(osquery.listening) == 0, 1, 0), closed_host = CASE(TO_LONG(osquery.listening) == 0, host.name, NULL) | STATS failed_nodes = SUM(closed), pool_nodes = COUNT_DISTINCT(host.name), nodes = VALUES(closed_host), latest_snapshot = MAX(@timestamp) BY logstash.pool, logstash.pipeline.name, port | WHERE failed_nodes > 0 | EVAL priority = CASE(failed_nodes == pool_nodes, \"P1\", \"P2\"), severity = CASE(failed_nodes == pool_nodes, \"critical\", \"warning\"), scope = CASE(failed_nodes == pool_nodes, \"the whole pool: the flow is cut\", \"part of the pool: the other nodes carry the flow, senders targeting these nodes lose their data\"), nodes = MV_CONCAT(nodes, \", \") | STATS failed_nodes = MAX(failed_nodes), pool_nodes = MAX(pool_nodes), nodes = MAX(nodes), scope = MAX(scope), severity = MAX(severity), latest_snapshot = MAX(latest_snapshot) BY logstash.pool, logstash.pipeline.name, port, priority | KEEP logstash.pool, logstash.pipeline.name, port, priority, severity, failed_nodes, pool_nodes, nodes, scope, latest_snapshot"}, "timeField": "@timestamp", "timeWindowSize": 6, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-pipe-input-port", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.nodes}}{{/context.hits}}", "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}", "severity": "{{#context.hits}}{{_source.severity}}{{/context.hits}}", "priority": "{{#context.hits}}{{_source.priority}}{{/context.hits}}", "status": "firing", "problem": "Input port {{#context.hits}}{{_source.port}}{{/context.hits}} closed on {{#context.hits}}{{_source.failed_nodes}}{{/context.hits}}/{{#context.hits}}{{_source.pool_nodes}}{{/context.hits}} node(s) of pool {{#context.hits}}{{_source.logstash.pool}}{{/context.hits}} ({{#context.hits}}{{_source.nodes}}{{/context.hits}}), {{#context.hits}}{{_source.scope}}{{/context.hits}}. Check the pipeline (health report, Address already in use logs).", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-pipe-input-port", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "", "pipeline": "", "severity": "ok", "priority": "P1/P2", "status": "recovered", "problem": "Input port open again", "time": "{{date}}"}]}}
  ]
}
```

#### `ls-pipe-queue-risk` – [P1/P2] Logstash pipeline – queue at risk

Pire indicateur sur 2 min par nœud et pipeline. **P1** : file persistante ≥ 50 % avec plus de 1 000 événements, ≥ 80 % sur disque, pleine dans moins de 30 min au rythme actuel, ou contre-pression de la file mémoire ≥ 0,5. **P2** : ≥ 20 % avec plus de 1 000 événements, ≥ 50 % sur disque, ou contre-pression ≥ 0,2.

Source : `metrics-logstash.pipeline-*` · fenêtre 5 min · confirmée après 2 exécution(s) · une alerte par `host.name` + `logstash.pipeline.name` + `priority`.
Message : *Queue at risk: persistent queue 30.2 % full, 20607 events waiting, full in 19.3 min. …*

```
POST kbn:/api/alerting/rule/ls-pipe-queue-risk
{
  "name": "[P1/P2] Logstash pipeline – queue at risk",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "pipeline", "elk-lab", "P1", "P2", "queue"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 2},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM metrics-logstash.pipeline-* | WHERE logstash.pipeline.name IS NOT NULL AND @timestamp > NOW() - 2 minutes | STATS used = MAX(logstash.pipeline.total.queues.current_size.bytes), max_bytes = MAX(logstash.pipeline.total.queues.max_size.bytes), events = MAX(logstash.pipeline.total.queues.events), queue_type = MAX(logstash.pipeline.total.queues.type), backpressure = AVG(logstash.pipeline.total.flow.queue_backpressure.last_1_minute), growth_events = AVG(logstash.pipeline.total.flow.queue_persisted_growth_events.last_1_minute), growth_bytes = AVG(logstash.pipeline.total.flow.queue_persisted_growth_bytes.last_1_minute) BY host.name, logstash.pipeline.name | EVAL pq = queue_type == \"persisted\" AND max_bytes > 0, fill_pct = CASE(pq, ROUND(100.0 * used / max_bytes, 1), NULL), minutes_to_full = CASE(pq AND events > 1000 AND growth_events > 0 AND growth_bytes > 0, ROUND(TO_DOUBLE(max_bytes - used) / growth_bytes / 60.0, 1), NULL), backpressure = CASE(queue_type == \"memory\", ROUND(backpressure, 2), NULL), p1 = COALESCE(fill_pct >= 50 AND events > 1000, false) OR COALESCE(fill_pct >= 80, false) OR COALESCE(minutes_to_full < 30, false) OR COALESCE(backpressure >= 0.5, false), p2 = COALESCE(fill_pct >= 20 AND events > 1000, false) OR COALESCE(fill_pct >= 50, false) OR COALESCE(backpressure >= 0.2, false), priority = CASE(p1, \"P1\", p2, \"P2\", NULL) | WHERE priority IS NOT NULL | EVAL severity = CASE(priority == \"P1\", \"critical\", \"warning\"), reason = CASE(queue_type == \"memory\", CONCAT(\"memory queue back-pressure \", TO_STRING(backpressure), \" (inputs blocked)\"), CONCAT(\"persistent queue \", TO_STRING(fill_pct), \" % full, \", TO_STRING(events), \" events waiting\", COALESCE(CONCAT(\", full in \", TO_STRING(minutes_to_full), \" min\"), \"\"))) | STATS reason = MAX(reason), severity = MAX(severity) BY host.name, logstash.pipeline.name, priority | KEEP host.name, logstash.pipeline.name, priority, severity, reason"}, "timeField": "@timestamp", "timeWindowSize": 5, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-pipe-queue-risk", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}", "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}", "severity": "{{#context.hits}}{{_source.severity}}{{/context.hits}}", "priority": "{{#context.hits}}{{_source.priority}}{{/context.hits}}", "status": "firing", "problem": "Queue at risk: {{#context.hits}}{{_source.reason}}{{/context.hits}}. The output can no longer keep up (Elasticsearch slow or blocked, downstream pipeline stopped): inputs will block, then logs may be lost.", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-pipe-queue-risk", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "", "pipeline": "", "severity": "ok", "priority": "P1/P2", "status": "recovered", "problem": "Queue back to normal", "time": "{{date}}"}]}}
  ]
}
```

#### `kb-ls-p1-index-rejected` – [P1] Logstash pipeline – data loss (events rejected by Elasticsearch)

« Could not index event » : Elasticsearch rejette définitivement des documents (conflit de mapping, erreur 400). **Perte de données** si aucune DLQ n'est configurée.

Source : `logs-logstash.log-*` · fenêtre 5 min · confirmée après 1 exécution(s) · une alerte par `host.name` + `logstash.pipeline.name`.
Message : *2 event(s) permanently rejected by Elasticsearch in 5 min (mapping conflict, error 400): data loss unless a dead letter queue (DLQ) is enabled.*

```
POST kbn:/api/alerting/rule/kb-ls-p1-index-rejected
{
  "name": "[P1] Logstash pipeline – data loss (events rejected by Elasticsearch)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "pipeline", "elk-lab", "P1"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 1},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM logs-logstash.log-* | WHERE data_stream.dataset == \"logstash.log\" AND QSTR(\"message:\\\"Could not index event\\\"\") | STATS rejected = COUNT(*) BY host.name, logstash.pipeline.name = logstash.log.pipeline_id"}, "timeField": "@timestamp", "timeWindowSize": 5, "timeWindowUnit": "m", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-index-rejected", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}", "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}", "severity": "critical", "priority": "P1", "status": "firing", "problem": "{{#context.hits}}{{_source.rejected}}{{/context.hits}} event(s) permanently rejected by Elasticsearch in 5 min (mapping conflict, error 400): data loss unless a dead letter queue (DLQ) is enabled.", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "kb-ls-p1-index-rejected", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "", "pipeline": "", "severity": "ok", "priority": "P1", "status": "recovered", "problem": "No more events rejected by Elasticsearch", "time": "{{date}}"}]}}
  ]
}
```

#### `ls-pipe-throughput` – [P1/P2] Logstash pipeline – critical pipeline throughput

Pour chaque pipeline de l'inventaire : débit d'entrée du **pool** (somme des nœuds, 5 dernières min). Alerte si sous `min_eps`, si des événements entrent sans rien sortir, ou si plus aucun nœud ne remonte le pipeline (vu dans les 6 dernières heures). Priorité lue dans l'inventaire.

Source : `metrics-logstash.pipeline-*` + lookup `logstash-pipelines-critiques` · fenêtre 6 h · confirmée après 3 exécution(s) · une alerte par `logstash.pool` + `logstash.pipeline.name`.
Message : *Critical pipeline (equipe-logs): input below the expected minimum. Pool input 12.4 ev/s (minimum 50), output 12.4 ev/s, 2 node(s) reporting, …*

```
POST kbn:/api/alerting/rule/ls-pipe-throughput
{
  "name": "[P1/P2] Logstash pipeline – critical pipeline throughput",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": ["logstash", "pipeline", "elk-lab", "P1", "P2", "throughput"],
  "schedule": {"interval": "1m"},
  "alert_delay": {"active": 3},
  "params": {"searchType": "esqlQuery", "esqlQuery": {"esql": "FROM metrics-logstash.pipeline-* | WHERE logstash.pipeline.name IS NOT NULL AND logstash.pool IS NOT NULL | EVAL recent = @timestamp > NOW() - 5 minutes, in_eps = CASE(recent, logstash.pipeline.total.flow.input_throughput.last_1_minute, NULL), out_eps = CASE(recent, logstash.pipeline.total.flow.output_throughput.last_1_minute, NULL) | STATS in_eps = AVG(in_eps), out_eps = AVG(out_eps), last_seen = MAX(@timestamp) BY logstash.pool, logstash.pipeline.name, host.name | EVAL in_eps = COALESCE(in_eps, 0.0), out_eps = COALESCE(out_eps, 0.0), node_live = CASE(last_seen > NOW() - 5 minutes, 1, 0) | STATS in_eps = SUM(in_eps), out_eps = SUM(out_eps), live_nodes = SUM(node_live), last_seen = MAX(last_seen) BY logstash.pool, logstash.pipeline.name | LOOKUP JOIN logstash-pipelines-critiques ON logstash.pipeline.name | WHERE min_eps IS NOT NULL | EVAL in_eps = ROUND(in_eps, 3), out_eps = ROUND(out_eps, 3), problem_kind = CASE(live_nodes == 0, \"no metrics from any node\", in_eps < min_eps, \"input below the expected minimum\", in_eps > 0 AND out_eps == 0, \"events received but nothing sent\", NULL) | WHERE problem_kind IS NOT NULL | EVAL severity = CASE(priority == \"P1\", \"critical\", \"warning\") | KEEP logstash.pool, logstash.pipeline.name, priority, severity, problem_kind, in_eps, out_eps, min_eps, live_nodes, last_seen, owner"}, "timeField": "@timestamp", "timeWindowSize": 6, "timeWindowUnit": "h", "threshold": [0], "thresholdComparator": ">", "size": 500, "groupBy": "row"},
  "actions": [
    {"id": "alerts-elklab-index", "group": "query matched", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-pipe-throughput", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "{{#context.hits}}{{_source.logstash.pool}}{{/context.hits}}", "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}", "severity": "{{#context.hits}}{{_source.severity}}{{/context.hits}}", "priority": "{{#context.hits}}{{_source.priority}}{{/context.hits}}", "status": "firing", "problem": "Critical pipeline ({{#context.hits}}{{_source.owner}}{{/context.hits}}): {{#context.hits}}{{_source.problem_kind}}{{/context.hits}}. Pool input {{#context.hits}}{{_source.in_eps}}{{/context.hits}} ev/s (minimum {{#context.hits}}{{_source.min_eps}}{{/context.hits}}), output {{#context.hits}}{{_source.out_eps}}{{/context.hits}} ev/s, {{#context.hits}}{{_source.live_nodes}}{{/context.hits}} node(s) reporting, last metrics {{#context.hits}}{{_source.last_seen}}{{/context.hits}}.", "time": "{{date}}"}]}},
    {"id": "alerts-elklab-index", "group": "recovered", "frequency": {"summary": false, "notify_when": "onActionGroupChange", "throttle": null}, "params": {"documents": [{"rule_id": "ls-pipe-throughput", "rule": "{{rule.name}}", "alert_id": "{{alert.id}}", "alert_uuid": "{{alert.uuid}}", "host": "", "pipeline": "", "severity": "ok", "priority": "P1/P2", "status": "recovered", "problem": "Critical pipeline throughput back to normal", "time": "{{date}}"}]}}
  ]
}
```

## 5. Grafana

### 5.1 Datasources

*Connections → Data sources → Add data source → Elasticsearch*, quatre fois. Pour toutes : URL d'un nœud
Elasticsearch, *Basic auth* avec `grafana_reader`, *With CA cert* (CA du cluster). Les **UID** sont référencés par le
dashboard (UID modifiable à la création uniquement, ou par provisioning).

| UID | Rôle | Index | Time field |
|---|---|---|---|
| `es-alerts-elklab` | Journal des alertes | `alerts-elklab` | `time` |
| `es-kibana-alerts` | Alertes Kibana, qualité des règles | `.alerts-*` | `@timestamp` |
| `es-logstash` | Métriques Logstash | `metrics-logstash.*` | `@timestamp` |
| `es-system` | Métriques système, fraîcheur des sources | `metrics-system.*` | `@timestamp` |

*Save & test* : « Elasticsearch data source is healthy ». Une requête ES|QL sans champ `metrics` dans sa cible
renvoie un résultat vide, sans erreur : le conserver si une requête est modifiée à la main.

### 5.2 Dashboard « Logstash – pipelines »

*Dashboards → New → Import*, coller le JSON, choisir le dossier. Variables : `pool`, `host`, `pipeline`.

| Panel | Type | Contenu |
|---|---|---|
| **Active alerts** | | |
| Total alerts | stat | All alerts still firing, whatever their priority: latest event of each alert in alerts-elklab is a firing (last 30 days) |
| Critical | stat | Critical (P1) alerts whose latest event in alerts-elklab is a firing (last 30 days) |
| Warning | stat | Warning (P2) alerts still firing, including the WARNING level of two-level rules (last 30 days) |
| Nodes in alert | stat | Number of distinct nodes (or pools) with an active alert |
| Oldest open alert | stat | Age of the oldest alert still firing: orange beyond 10 min, red beyond 1 h (alert not handled) |
| Active alerts | table | Active Kibana alerts, from the alerts-elklab log: latest event of each alert (alert_id), kept if it is "firing" |
| Alert history | table | alerts-elklab log over the dashboard time range: one event per firing (with its severity) and per recovery (recovered) |
| New alerts per rule (time fired) | timeseries | Kibana alerts of the Logstash rules (.alerts-* index, rules tagged logstash) |
| Alert history for the period | table | Kibana alerts of the Logstash rules (.alerts-* index, rules tagged logstash) |
| **Collection: source freshness** | | |
| Data source freshness (last data received) | table | Last data received per Logstash node (hosts tagged logstash.pool) and per source (Elastic Agent, Metricbeat, Filebeat, Logstash flattening, Osquery, Synthetics) |
| **Alerting: rule quality** | | |
| Noisiest rules (7 days) | bargauge | Number of firings per rule over 7 days (.alerts-* index) |
| Alert duration per rule (7 days) | table | Per rule over 7 days: firings, active alerts, average and maximum duration (time to recover), last firing |
| Alert rule health (24 hours) | table | Rule executions from the Kibana event log (.kibana-event-log-*) |
| **Throughput** | | |
| Input throughput (events/s) | timeseries |  |
| Output throughput (events/s) | timeseries |  |
| **Persistent queues** | | |
| Persistent queue fill (%) | timeseries | Size / maximum size |
| Events waiting in persistent queues | timeseries |  |
| Queue growth (events/s) | timeseries | > 0: the queue is filling up (the output cannot keep up) |
| **Saturation** | | |
| Back-pressure (queue_backpressure) | timeseries | Time spent by inputs waiting for room in the queue |
| Worker utilization (%) | timeseries |  |
| **Health** | | |
| Health report: latest state per pipeline (red = no measurement for more than 90 s) | table | Last known state of each pipeline (status, state and symptom of the most recent report) |
| Configuration reload failures (per interval) | timeseries | Increase of the reloads.failures counter: configuration rejected by Logstash (the previous version keeps running, but will not restart) |
| JVM heap per node (%) | timeseries |  |
| **Logstash process** | | |
| Logstash process memory per node (RSS, JVM heap dashed) | timeseries | RSS: memory actually used by the Logstash process (heap + off-heap + thread stacks + native buffers), measured by a System integration instance dedicated to the Logstash process only (metrics-system.process-logstash, no top N: always collected) |
| Logstash process CPU per node (%) | timeseries | CPU used by the Logstash process, as published by Logstash itself (API :9600, logstash.node.stats.process.cpu.percent, as % of the machine total capacity) |

Annotations : déclenchements P1 (rouge), P2 (orange) et levées (vert), sur tous les graphiques temporels.

```json
{
  "uid": "elk-lab-logstash-pipelines",
  "title": "Logstash – pipelines",
  "description": "Logstash pipelines from Elastic Agent metrics (metrics-logstash.pipeline/node/health_report). Provisioned by elk-lab/grafana/deploy.sh.",
  "tags": ["elk-lab", "logstash"],
  "timezone": "browser",
  "schemaVersion": 39,
  "version": 11,
  "editable": false,
  "graphTooltip": 1,
  "refresh": "30s",
  "time": {"from": "now-1h", "to": "now"},
  "links": [{"title": "Grafana alert rules", "type": "link", "url": "/alerting/list?search=folder:elk-lab", "icon": "bell"}],
  "templating": {"list": [{"name": "pool", "label": "Pool", "type": "query", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "{\"find\": \"terms\", \"field\": \"logstash.pool\", \"query\": \"data_stream.dataset:logstash.pipeline\"}", "refresh": 2, "multi": true, "includeAll": true, "allValue": "*", "current": {"selected": true, "text": ["All"], "value": ["$__all"]}, "sort": 1}, {"name": "host", "label": "Node", "type": "query", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "{\"find\": \"terms\", \"field\": \"host.name\", \"query\": \"data_stream.dataset:logstash.pipeline AND logstash.pool:${pool:lucene}\"}", "refresh": 2, "multi": true, "includeAll": true, "allValue": "*", "current": {"selected": true, "text": ["All"], "value": ["$__all"]}, "sort": 1}, {"name": "pipeline", "label": "Pipeline", "type": "query", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "{\"find\": \"terms\", \"field\": \"logstash.pipeline.name\", \"query\": \"data_stream.dataset:logstash.pipeline AND logstash.pool:${pool:lucene} AND host.name:${host:lucene}\", \"size\": 500}", "refresh": 2, "multi": true, "includeAll": true, "allValue": "*", "current": {"selected": true, "text": ["All"], "value": ["$__all"]}, "sort": 1}]},
  "panels": [
    {"id": 200, "type": "row", "title": "Active alerts", "gridPos": {"x": 0, "y": 0, "w": 24, "h": 1}, "collapsed": false, "panels": []},
    {"id": 39, "type": "stat", "title": "Total alerts", "description": "All alerts still firing, whatever their priority: latest event of each alert in alerts-elklab is a firing (last 30 days).", "gridPos": {"x": 0, "y": 1, "w": 4, "h": 4}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "queryType": "esql", "editorType": "code", "query": "FROM alerts-elklab | WHERE time >= NOW() - 30 days | INLINE STATS latest = MAX(time) BY alert_id, rule_id | WHERE time == latest AND status == \"firing\" | STATS n = COUNT(*)", "timeField": "time", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "500"}}]}], "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": false}, "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"}, "fieldConfig": {"defaults": {"unit": "none", "noValue": "none", "decimals": 0, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 1}]}, "color": {"mode": "thresholds"}}, "overrides": []}},
    {"id": 40, "type": "stat", "title": "Critical", "description": "Critical (P1) alerts whose latest event in alerts-elklab is a firing (last 30 days).", "gridPos": {"x": 4, "y": 1, "w": 5, "h": 4}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "queryType": "esql", "editorType": "code", "query": "FROM alerts-elklab | WHERE time >= NOW() - 30 days | INLINE STATS latest = MAX(time) BY alert_id, rule_id | WHERE time == latest AND status == \"firing\" | WHERE priority == \"P1\" | STATS n = COUNT(*)", "timeField": "time", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "500"}}]}], "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": false}, "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"}, "fieldConfig": {"defaults": {"unit": "none", "noValue": "none", "decimals": 0, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "red", "value": 1}]}, "color": {"mode": "thresholds"}}, "overrides": []}},
    {"id": 41, "type": "stat", "title": "Warning", "description": "Warning (P2) alerts still firing, including the WARNING level of two-level rules (last 30 days).", "gridPos": {"x": 9, "y": 1, "w": 5, "h": 4}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "queryType": "esql", "editorType": "code", "query": "FROM alerts-elklab | WHERE time >= NOW() - 30 days | INLINE STATS latest = MAX(time) BY alert_id, rule_id | WHERE time == latest AND status == \"firing\" | WHERE priority == \"P2\" | STATS n = COUNT(*)", "timeField": "time", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "500"}}]}], "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": false}, "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"}, "fieldConfig": {"defaults": {"unit": "none", "noValue": "none", "decimals": 0, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 1}]}, "color": {"mode": "thresholds"}}, "overrides": []}},
    {"id": 42, "type": "stat", "title": "Nodes in alert", "description": "Number of distinct nodes (or pools) with an active alert.", "gridPos": {"x": 14, "y": 1, "w": 5, "h": 4}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "queryType": "esql", "editorType": "code", "query": "FROM alerts-elklab | WHERE time >= NOW() - 30 days | INLINE STATS latest = MAX(time) BY alert_id, rule_id | WHERE time == latest AND status == \"firing\" | STATS n = COUNT_DISTINCT(host)", "timeField": "time", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "500"}}]}], "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": false}, "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"}, "fieldConfig": {"defaults": {"unit": "none", "noValue": "none", "decimals": 0, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 1}]}, "color": {"mode": "thresholds"}}, "overrides": []}},
    {"id": 43, "type": "stat", "title": "Oldest open alert", "description": "Age of the oldest alert still firing: orange beyond 10 min, red beyond 1 h (alert not handled).", "gridPos": {"x": 19, "y": 1, "w": 5, "h": 4}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "queryType": "esql", "editorType": "code", "query": "FROM alerts-elklab | WHERE time >= NOW() - 30 days | INLINE STATS latest = MAX(time) BY alert_id, rule_id | WHERE time == latest AND status == \"firing\" | STATS oldest = MIN(time) | EVAL age_s = DATE_DIFF(\"second\", oldest, NOW()) | KEEP age_s", "timeField": "time", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "500"}}]}], "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": false}, "colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center"}, "fieldConfig": {"defaults": {"unit": "s", "noValue": "none", "decimals": 0, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 600}, {"color": "red", "value": 3600}]}, "color": {"mode": "thresholds"}}, "overrides": []}},
    {"id": 22, "type": "table", "title": "Active alerts", "description": "Active Kibana alerts, from the alerts-elklab log: latest event of each alert (alert_id), kept if it is \"firing\". Readable message (Problem) written by each rule. The last 30 days are always taken into account, whatever the dashboard time range.", "gridPos": {"x": 0, "y": 5, "w": 24, "h": 7}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "queryType": "esql", "editorType": "code", "query": "FROM alerts-elklab\n| WHERE time >= NOW() - 30 days\n| INLINE STATS latest = MAX(time) BY alert_id, rule_id\n| WHERE time == latest AND status == \"firing\"\n| EVAL Priority = priority, Severity = severity, Host = host, Pipeline = pipeline,\n       Problem = REPLACE(problem, \"([0-9]) B \", \"$1 % \"),\n       Rule = REPLACE(rule, \"^\\\\[[^\\\\]]*\\\\] \", \"\"), Time = time\n| KEEP Priority, Severity, Host, Pipeline, Problem, Time, Rule\n| SORT Priority ASC, Time DESC\n| LIMIT 500", "timeField": "time", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "1000"}}]}], "transformations": [], "options": {"showHeader": true, "cellHeight": "sm", "footer": {"show": true, "reducer": ["count"], "fields": ["Problem"], "countRows": true}}, "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": true, "filterable": true}}, "overrides": [{"matcher": {"id": "byName", "options": "Priority"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}, {"id": "custom.width", "value": 80}, {"id": "mappings", "value": [{"type": "value", "options": {"P1": {"color": "red", "index": 0}, "P2": {"color": "orange", "index": 1}, "P3": {"color": "blue", "index": 2}, "P2/P1": {"color": "text", "index": 3}}}]}]}, {"matcher": {"id": "byName", "options": "Severity"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text", "mode": "basic"}}, {"id": "custom.width", "value": 90}, {"id": "mappings", "value": [{"type": "value", "options": {"critical": {"color": "red", "index": 0}, "warning": {"color": "orange", "index": 1}, "ok": {"color": "green", "index": 2}, "info": {"color": "blue", "index": 3}}}]}]}, {"matcher": {"id": "byName", "options": "Time"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 170}]}, {"matcher": {"id": "byName", "options": "Host"}, "properties": [{"id": "custom.width", "value": 150}]}, {"matcher": {"id": "byName", "options": "Pipeline"}, "properties": [{"id": "custom.width", "value": 190}]}, {"matcher": {"id": "byName", "options": "Rule"}, "properties": [{"id": "custom.width", "value": 300}]}]}},
    {"id": 23, "type": "table", "title": "Alert history", "gridPos": {"x": 0, "y": 12, "w": 24, "h": 9}, "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "queryType": "esql", "editorType": "code", "query": "FROM alerts-elklab\n| INLINE STATS h = MAX(host), p = MAX(pipeline) BY alert_id, rule_id\n| EVAL fired_ts = CASE(status == \"firing\", time, NULL), recovered_ts = CASE(status == \"recovered\", time, NULL)\n| INLINE STATS fired = MIN(fired_ts), recovered = MAX(recovered_ts) BY alert_uuid\n| EVAL Host = CASE(host IS NULL OR host == \"\", h, host),\n       Pipeline = CASE(pipeline IS NULL OR pipeline == \"\", p, pipeline),\n       Status = status, Priority = priority,\n       Duration = CASE(alert_uuid IS NULL OR fired IS NULL, NULL,\n                       recovered IS NULL, DATE_DIFF(\"second\", fired, NOW()),\n                       DATE_DIFF(\"second\", fired, recovered)),\n       Problem = REPLACE(problem, \"([0-9]) B \", \"$1 % \"),\n       Rule = REPLACE(rule, \"^\\\\[[^\\\\]]*\\\\] \", \"\"), Time = time\n| KEEP Time, Status, Priority, Duration, Host, Pipeline, Problem, Rule\n| SORT Time DESC\n| LIMIT 1000", "timeField": "time", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "1000"}}]}], "transformations": [], "options": {"showHeader": true, "cellHeight": "sm", "footer": {"show": true, "reducer": ["count"], "fields": ["Problem"], "countRows": true}}, "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": true, "filterable": true}}, "overrides": [{"matcher": {"id": "byName", "options": "Status"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text", "mode": "basic"}}, {"id": "custom.width", "value": 95}, {"id": "mappings", "value": [{"type": "value", "options": {"firing": {"color": "orange", "index": 0}, "recovered": {"color": "green", "index": 1}}}]}]}, {"matcher": {"id": "byName", "options": "Priority"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}, {"id": "custom.width", "value": 80}, {"id": "mappings", "value": [{"type": "value", "options": {"P1": {"color": "red", "index": 0}, "P2": {"color": "orange", "index": 1}, "P3": {"color": "blue", "index": 2}, "P2/P1": {"color": "text", "index": 3}}}]}]}, {"matcher": {"id": "byName", "options": "Duration"}, "properties": [{"id": "unit", "value": "s"}, {"id": "custom.width", "value": 100}, {"id": "decimals", "value": 0}]}, {"matcher": {"id": "byName", "options": "Time"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 170}]}, {"matcher": {"id": "byName", "options": "Host"}, "properties": [{"id": "custom.width", "value": 150}]}, {"matcher": {"id": "byName", "options": "Pipeline"}, "properties": [{"id": "custom.width", "value": 190}]}, {"matcher": {"id": "byName", "options": "Rule"}, "properties": [{"id": "custom.width", "value": 300}]}]}, "description": "alerts-elklab log over the dashboard time range: one event per firing (with its severity) and per recovery (recovered). Node and pipeline of recoveries are taken from the matching firing. Duration: total duration of the alert (from firing to recovery); for an alert still firing, time elapsed since it fired. Empty for events written before 2026-10-13 (no alert_uuid)."},
    {"id": 24, "type": "timeseries", "title": "New alerts per rule (time fired)", "gridPos": {"x": 0, "y": 21, "w": 24, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-kibana-alerts"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-kibana-alerts"}, "query": "kibana.alert.rule.tags:logstash AND NOT kibana.alert.status:untracked", "metrics": [{"id": "1", "type": "count"}], "bucketAggs": [{"id": "3", "type": "terms", "field": "kibana.alert.rule.name", "settings": {"size": "10", "order": "desc", "orderBy": "_count", "min_doc_count": "1"}}, {"id": "2", "type": "date_histogram", "field": "kibana.alert.start", "settings": {"interval": "auto", "min_doc_count": "0"}}], "timeField": "@timestamp"}], "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": true}, "tooltip": {"mode": "multi", "sort": "desc"}}, "fieldConfig": {"defaults": {"unit": "none", "decimals": 0, "min": 0, "custom": {"drawStyle": "bars", "fillOpacity": 80, "stacking": {"mode": "normal", "group": "A"}, "lineWidth": 1, "showPoints": "never"}}, "overrides": []}, "description": "Kibana alerts of the Logstash rules (.alerts-* index, rules tagged logstash)."},
    {"id": 25, "type": "table", "title": "Alert history for the period", "gridPos": {"x": 0, "y": 29, "w": 24, "h": 9}, "datasource": {"type": "elasticsearch", "uid": "es-kibana-alerts"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-kibana-alerts"}, "query": "kibana.alert.rule.tags:logstash", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "500"}}], "bucketAggs": [], "timeField": "@timestamp"}], "transformations": [{"id": "filterFieldsByName", "options": {"include": {"names": ["kibana.alert.start", "kibana.alert.end", "kibana.alert.duration.us", "kibana.alert.status", "kibana.alert.rule.name", "kibana.alert.instance.id", "kibana.alert.evaluation.values"]}}}, {"id": "convertFieldType", "options": {"conversions": [{"targetField": "kibana.alert.start", "destinationType": "time"}, {"targetField": "kibana.alert.end", "destinationType": "time"}]}}, {"id": "organize", "options": {"indexByName": {"kibana.alert.start": 0, "kibana.alert.end": 1, "kibana.alert.duration.us": 2, "kibana.alert.status": 3, "kibana.alert.rule.name": 4, "kibana.alert.instance.id": 5, "kibana.alert.evaluation.values": 6}, "renameByName": {"kibana.alert.start": "Start", "kibana.alert.end": "End", "kibana.alert.duration.us": "Duration", "kibana.alert.status": "Status", "kibana.alert.rule.name": "Rule", "kibana.alert.instance.id": "Node, pipeline", "kibana.alert.evaluation.values": "Value"}}}, {"id": "sortBy", "options": {"sort": [{"field": "Start", "desc": true}]}}], "options": {"showHeader": true, "cellHeight": "sm"}, "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}}, "overrides": [{"matcher": {"id": "byName", "options": "Duration"}, "properties": [{"id": "unit", "value": "µs"}, {"id": "decimals", "value": 0}]}, {"matcher": {"id": "byName", "options": "Status"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "mappings", "value": [{"type": "value", "options": {"active": {"text": "active", "color": "red", "index": 0}, "recovered": {"text": "recovered", "color": "green", "index": 1}, "untracked": {"text": "untracked", "color": "text", "index": 2}}}]}]}]}, "description": "Kibana alerts of the Logstash rules (.alerts-* index, rules tagged logstash)."},
    {"id": 105, "type": "row", "title": "Collection: source freshness", "collapsed": false, "gridPos": {"x": 0, "y": 38, "w": 24, "h": 1}, "panels": []},
    {"id": 44, "type": "table", "title": "Data source freshness (last data received)", "description": "Last data received per Logstash node (hosts tagged logstash.pool) and per source (Elastic Agent, Metricbeat, Filebeat, Logstash flattening, Osquery, Synthetics). OK / LATE / SILENT according to the expected frequency of the source (10 s metrics: OK < 60 s, silent > 5 min; Synthetics: OK < 2 min; Osquery every 2 min: OK < 4 min, silent > 10 min). Logs are \"event-driven\": they only arrive when Logstash writes. A source SILENT on all nodes means a collection outage (network, DNS, Elasticsearch) that no alert rule can see.", "gridPos": {"x": 0, "y": 39, "w": 24, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-system"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-system"}, "queryType": "esql", "editorType": "code", "query": "FROM metrics-system.*,metricbeat-*,metrics-logstash.*,metrics-logstash_flat.*,logs-logstash.log-*,filebeat-*,logs-osquery_manager.result-*,synthetics-* METADATA _index\n| EVAL logs = _index LIKE \"*filebeat*\" OR _index LIKE \"*logs-logstash*\"\n| WHERE (@timestamp >= NOW() - 24 hours OR (logs AND @timestamp >= NOW() - 7 days))\n    AND (logstash.pool IS NOT NULL OR _index LIKE \"*synthetics*\")\n| EVAL Source = CASE(\n    _index LIKE \"*metricbeat*\", \"Metricbeat – system\",\n    _index LIKE \"*metrics-system*\", \"Elastic Agent – system\",\n    _index LIKE \"*logstash_flat*\", \"Logstash flattening (Beats)\",\n    _index LIKE \"*metrics-logstash*\", \"Elastic Agent – Logstash metrics\",\n    _index LIKE \"*filebeat*\", \"Filebeat – Logstash logs\",\n    _index LIKE \"*logs-logstash*\", \"Elastic Agent – Logstash logs\",\n    _index LIKE \"*osquery*\", \"Osquery\",\n    _index LIKE \"*synthetics*\", \"Synthetics (probe)\",\n    \"other\"),\n    Node = CASE(_index LIKE \"*synthetics*\", COALESCE(observer.geo.name, \"probe\"), COALESCE(host.name, logstash.node.name))\n| WHERE Node IS NOT NULL\n| STATS latest = MAX(@timestamp), logs = MAX(CASE(logs, 1, 0)) BY Source, Node\n| EVAL `Age (s)` = DATE_DIFF(\"second\", latest, NOW()),\n       ok_s = CASE(Source == \"Osquery\", 240, Source LIKE \"Synthetics*\", 120, 60),\n       silent_s = CASE(Source == \"Osquery\", 600, 300),\n       Status = CASE(logs == 1, \"event-driven\", `Age (s)` < ok_s, \"OK\", `Age (s)` < silent_s, \"LATE\", \"SILENT\"),\n       `Last data` = latest\n| EVAL sort_order = CASE(Status == \"SILENT\", 0, Status == \"LATE\", 1, Status == \"OK\", 2, 3)\n| SORT sort_order ASC, `Age (s)` DESC\n| KEEP Status, Node, Source, `Last data`, `Age (s)`", "timeField": "@timestamp", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "500"}}]}], "transformations": [], "options": {"showHeader": true, "cellHeight": "sm", "footer": {"show": false}}, "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "filterable": true}}, "overrides": [{"matcher": {"id": "byName", "options": "Status"}, "properties": [{"id": "custom.width", "value": 110}, {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}, {"id": "mappings", "value": [{"type": "value", "options": {"OK": {"color": "green", "index": 0}, "LATE": {"color": "orange", "index": 1}, "SILENT": {"color": "red", "index": 2}, "event-driven": {"color": "text", "index": 3}}}]}]}, {"matcher": {"id": "byName", "options": "Last data"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 180}]}, {"matcher": {"id": "byName", "options": "Age (s)"}, "properties": [{"id": "unit", "value": "s"}, {"id": "custom.width", "value": 110}]}, {"matcher": {"id": "byName", "options": "Node"}, "properties": [{"id": "custom.width", "value": 130}]}]}},
    {"id": 106, "type": "row", "title": "Alerting: rule quality", "collapsed": false, "gridPos": {"x": 0, "y": 47, "w": 24, "h": 1}, "panels": []},
    {"id": 50, "type": "bargauge", "title": "Noisiest rules (7 days)", "timeFrom": "7d", "description": "Number of firings per rule over 7 days (.alerts-* index). A very noisy rule needs tuning: threshold, window, confirmations, or a recurring cause to fix.", "gridPos": {"x": 0, "y": 48, "w": 12, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-kibana-alerts"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-kibana-alerts"}, "queryType": "esql", "editorType": "code", "query": "FROM .alerts-*\n| WHERE MV_CONTAINS(kibana.alert.rule.tags, \"elk-lab\") AND kibana.alert.status != \"untracked\"\n| STATS `Firings` = COUNT(*) BY `Rule` = kibana.alert.rule.name\n| SORT `Firings` DESC\n| LIMIT 10", "timeField": "@timestamp", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "500"}}]}], "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": true, "valueMode": "color", "namePlacement": "left", "sizing": "auto", "reduceOptions": {"values": true, "calcs": ["lastNotNull"], "fields": "/^Firings$/"}}, "fieldConfig": {"defaults": {"unit": "none", "min": 0, "decimals": 0, "color": {"mode": "continuous-YlRd"}, "displayName": "${__data.fields[\"Rule\"]}"}, "overrides": []}},
    {"id": 51, "type": "table", "title": "Alert duration per rule (7 days)", "timeFrom": "7d", "description": "Per rule over 7 days: firings, active alerts, average and maximum duration (time to recover), last firing. A very long maximum duration means an alert left open without being handled.", "gridPos": {"x": 12, "y": 48, "w": 12, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-kibana-alerts"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-kibana-alerts"}, "queryType": "esql", "editorType": "code", "query": "FROM .alerts-*\n| WHERE MV_CONTAINS(kibana.alert.rule.tags, \"elk-lab\") AND kibana.alert.status != \"untracked\"\n| EVAL duration_min = kibana.alert.duration.us / 60000000.0, active = CASE(kibana.alert.status == \"active\", 1, 0)\n| STATS `Firings` = COUNT(*), `Active` = SUM(active), `Avg duration` = ROUND(AVG(duration_min), 1),\n        `Max duration` = ROUND(MAX(duration_min), 1), `Last fired` = MAX(kibana.alert.start)\n     BY `Rule` = kibana.alert.rule.name\n| SORT `Avg duration` DESC\n| LIMIT 50", "timeField": "@timestamp", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "500"}}]}], "transformations": [], "options": {"showHeader": true, "cellHeight": "sm", "footer": {"show": false}}, "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "filterable": true}}, "overrides": [{"matcher": {"id": "byName", "options": "Avg duration"}, "properties": [{"id": "unit", "value": "m"}, {"id": "decimals", "value": 1}, {"id": "custom.width", "value": 115}]}, {"matcher": {"id": "byName", "options": "Max duration"}, "properties": [{"id": "unit", "value": "m"}, {"id": "decimals", "value": 1}, {"id": "custom.width", "value": 105}, {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "text", "value": null}, {"color": "orange", "value": 60}, {"color": "red", "value": 240}]}}]}, {"matcher": {"id": "byName", "options": "Last fired"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 170}]}, {"matcher": {"id": "byName", "options": "Active"}, "properties": [{"id": "custom.width", "value": 80}, {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "text", "value": null}, {"color": "red", "value": 1}]}}]}, {"matcher": {"id": "byName", "options": "Firings"}, "properties": [{"id": "custom.width", "value": 120}]}]}},
    {"id": 52, "type": "table", "title": "Alert rule health (24 hours)", "timeFrom": "24h", "description": "Rule executions from the Kibana event log (.kibana-event-log-*). FAILING: at least one failed execution in the last 15 minutes. NOT RUNNING: no execution for more than 5 min (rule stuck or recently disabled). A failing rule no longer fires anything: this is the monitoring of the monitoring.", "gridPos": {"x": 0, "y": 56, "w": 24, "h": 9}, "datasource": {"type": "elasticsearch", "uid": "es-kibana-alerts"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-kibana-alerts"}, "queryType": "esql", "editorType": "code", "query": "FROM .kibana-event-log-*\n| WHERE @timestamp >= NOW() - 24 hours AND event.provider == \"alerting\" AND event.action == \"execute\"\n    AND (rule.id LIKE \"kb-*\" OR rule.id LIKE \"logstash-*\" OR rule.id LIKE \"ls-*\")\n| EVAL failed = CASE(event.outcome == \"failure\", 1, 0),\n       failed_15m = CASE(event.outcome == \"failure\" AND @timestamp >= NOW() - 15 minutes, 1, 0),\n       error_msg = CASE(event.outcome == \"failure\", TO_STRING(error.message), NULL),\n       duration_ms = event.duration / 1000000.0\n| STATS `Last execution` = MAX(@timestamp), `Executions (24 h)` = COUNT(*), `Failures (24 h)` = SUM(failed),\n        failures_15m = SUM(failed_15m), `Avg duration (ms)` = ROUND(AVG(duration_ms), 0), `Max duration (ms)` = ROUND(MAX(duration_ms), 0),\n        `Last error` = MAX(error_msg)\n     BY `Rule` = rule.name\n| EVAL age_s = DATE_DIFF(\"second\", `Last execution`, NOW()),\n       Status = CASE(age_s > 300, \"NOT RUNNING\", failures_15m > 0, \"FAILING\", \"OK\"),\n       sort_order = CASE(Status == \"NOT RUNNING\", 0, Status == \"FAILING\", 1, 2)\n| SORT sort_order ASC, `Failures (24 h)` DESC, `Rule` ASC\n| KEEP Status, `Rule`, `Last execution`, `Executions (24 h)`, `Failures (24 h)`, `Avg duration (ms)`, `Max duration (ms)`, `Last error`", "timeField": "@timestamp", "metrics": [{"id": "1", "type": "raw_data", "settings": {"size": "500"}}]}], "transformations": [], "options": {"showHeader": true, "cellHeight": "sm", "footer": {"show": false}}, "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "filterable": true}}, "overrides": [{"matcher": {"id": "byName", "options": "Status"}, "properties": [{"id": "custom.width", "value": 140}, {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}, {"id": "mappings", "value": [{"type": "value", "options": {"OK": {"color": "green", "index": 0}, "FAILING": {"color": "red", "index": 1}, "NOT RUNNING": {"color": "orange", "index": 2}}}]}]}, {"matcher": {"id": "byName", "options": "Last execution"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 170}]}, {"matcher": {"id": "byName", "options": "Failures (24 h)"}, "properties": [{"id": "custom.width", "value": 110}, {"id": "custom.cellOptions", "value": {"type": "color-text"}}, {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "text", "value": null}, {"color": "red", "value": 1}]}}]}, {"matcher": {"id": "byName", "options": "Avg duration (ms)"}, "properties": [{"id": "unit", "value": "ms"}, {"id": "custom.width", "value": 140}]}, {"matcher": {"id": "byName", "options": "Max duration (ms)"}, "properties": [{"id": "unit", "value": "ms"}, {"id": "custom.width", "value": 120}]}, {"matcher": {"id": "byName", "options": "Executions (24 h)"}, "properties": [{"id": "custom.width", "value": 130}]}]}},
    {"id": 100, "type": "row", "title": "Throughput", "gridPos": {"x": 0, "y": 65, "w": 24, "h": 1}, "collapsed": false, "panels": []},
    {"id": 1, "type": "timeseries", "title": "Input throughput (events/s)", "gridPos": {"x": 0, "y": 66, "w": 12, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.pipeline AND logstash.pool:${pool:lucene} AND host.name:${host:lucene} AND logstash.pipeline.name:${pipeline:lucene}", "alias": "{{term host.name}} – {{term logstash.pipeline.name}}", "metrics": [{"id": "1", "type": "avg", "field": "logstash.pipeline.total.flow.input_throughput.last_1_minute"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "3", "type": "terms", "field": "logstash.pipeline.name", "settings": {"size": "100", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto", "min_doc_count": "0"}}], "timeField": "@timestamp"}], "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}}, "fieldConfig": {"defaults": {"unit": "short", "min": 0, "custom": {"lineWidth": 1, "fillOpacity": 5, "showPoints": "never", "spanNulls": true}}, "overrides": []}},
    {"id": 2, "type": "timeseries", "title": "Output throughput (events/s)", "gridPos": {"x": 12, "y": 66, "w": 12, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.pipeline AND logstash.pool:${pool:lucene} AND host.name:${host:lucene} AND logstash.pipeline.name:${pipeline:lucene}", "alias": "{{term host.name}} – {{term logstash.pipeline.name}}", "metrics": [{"id": "1", "type": "avg", "field": "logstash.pipeline.total.flow.output_throughput.last_1_minute"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "3", "type": "terms", "field": "logstash.pipeline.name", "settings": {"size": "100", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto", "min_doc_count": "0"}}], "timeField": "@timestamp"}], "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}}, "fieldConfig": {"defaults": {"unit": "short", "min": 0, "custom": {"lineWidth": 1, "fillOpacity": 5, "showPoints": "never", "spanNulls": true}}, "overrides": []}},
    {"id": 101, "type": "row", "title": "Persistent queues", "gridPos": {"x": 0, "y": 74, "w": 24, "h": 1}, "collapsed": false, "panels": []},
    {"id": 3, "type": "timeseries", "title": "Persistent queue fill (%)", "description": "Size / maximum size. Note: the size includes the current page (sawtooth from 0 to one page even when the queue is empty); see \"Events waiting\".", "gridPos": {"x": 0, "y": 75, "w": 12, "h": 8}, "datasource": {"type": "datasource", "uid": "-- Mixed --"}, "targets": [{"refId": "A", "hide": true, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.pipeline AND logstash.pipeline.total.queues.type:persisted AND logstash.pool:${pool:lucene} AND host.name:${host:lucene} AND logstash.pipeline.name:${pipeline:lucene}", "metrics": [{"id": "1", "type": "max", "field": "logstash.pipeline.total.queues.current_size.bytes"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "3", "type": "terms", "field": "logstash.pipeline.name", "settings": {"size": "100", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "1m", "min_doc_count": "1"}}], "timeField": "@timestamp"}, {"refId": "B", "hide": true, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.pipeline AND logstash.pipeline.total.queues.type:persisted AND logstash.pool:${pool:lucene} AND host.name:${host:lucene} AND logstash.pipeline.name:${pipeline:lucene}", "metrics": [{"id": "1", "type": "max", "field": "logstash.pipeline.total.queues.max_size.bytes"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "3", "type": "terms", "field": "logstash.pipeline.name", "settings": {"size": "100", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "1m", "min_doc_count": "1"}}], "timeField": "@timestamp"}, {"refId": "C", "datasource": {"type": "__expr__", "uid": "__expr__"}, "type": "math", "expression": "$A / $B * 100"}], "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}}, "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "displayName": "${__field.labels[\"host.name\"]} – ${__field.labels[\"logstash.pipeline.name\"]}", "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 50}, {"color": "red", "value": 80}]}, "custom": {"lineWidth": 1, "fillOpacity": 5, "showPoints": "never", "spanNulls": true, "thresholdsStyle": {"mode": "line+area"}}}, "overrides": []}},
    {"id": 4, "type": "timeseries", "title": "Events waiting in persistent queues", "gridPos": {"x": 12, "y": 75, "w": 12, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.pipeline AND logstash.pipeline.total.queues.type:persisted AND logstash.pool:${pool:lucene} AND host.name:${host:lucene} AND logstash.pipeline.name:${pipeline:lucene}", "alias": "{{term host.name}} – {{term logstash.pipeline.name}}", "metrics": [{"id": "1", "type": "max", "field": "logstash.pipeline.total.queues.events"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "3", "type": "terms", "field": "logstash.pipeline.name", "settings": {"size": "100", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto", "min_doc_count": "0"}}], "timeField": "@timestamp"}], "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}}, "fieldConfig": {"defaults": {"unit": "short", "min": 0, "custom": {"lineWidth": 1, "fillOpacity": 10, "showPoints": "never", "spanNulls": true}}, "overrides": []}},
    {"id": 5, "type": "timeseries", "title": "Queue growth (events/s)", "description": "> 0: the queue is filling up (the output cannot keep up). < 0: it is draining.", "gridPos": {"x": 0, "y": 83, "w": 24, "h": 7}, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.pipeline AND logstash.pipeline.total.queues.type:persisted AND logstash.pool:${pool:lucene} AND host.name:${host:lucene} AND logstash.pipeline.name:${pipeline:lucene}", "alias": "{{term host.name}} – {{term logstash.pipeline.name}}", "metrics": [{"id": "1", "type": "avg", "field": "logstash.pipeline.total.flow.queue_persisted_growth_events.last_1_minute"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "3", "type": "terms", "field": "logstash.pipeline.name", "settings": {"size": "100", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto", "min_doc_count": "0"}}], "timeField": "@timestamp"}], "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}}, "fieldConfig": {"defaults": {"unit": "short", "custom": {"lineWidth": 1, "fillOpacity": 5, "showPoints": "never", "spanNulls": true}}, "overrides": []}},
    {"id": 102, "type": "row", "title": "Saturation", "gridPos": {"x": 0, "y": 90, "w": 24, "h": 1}, "collapsed": false, "panels": []},
    {"id": 6, "type": "timeseries", "title": "Back-pressure (queue_backpressure)", "description": "Time spent by inputs waiting for room in the queue. Sustained above 0.5: alert.", "gridPos": {"x": 0, "y": 91, "w": 12, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.pipeline AND logstash.pool:${pool:lucene} AND host.name:${host:lucene} AND logstash.pipeline.name:${pipeline:lucene}", "alias": "{{term host.name}} – {{term logstash.pipeline.name}}", "metrics": [{"id": "1", "type": "avg", "field": "logstash.pipeline.total.flow.queue_backpressure.last_1_minute"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "3", "type": "terms", "field": "logstash.pipeline.name", "settings": {"size": "100", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto", "min_doc_count": "0"}}], "timeField": "@timestamp"}], "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}}, "fieldConfig": {"defaults": {"unit": "short", "min": 0, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "red", "value": 0.5}]}, "custom": {"lineWidth": 1, "fillOpacity": 5, "showPoints": "never", "spanNulls": true, "thresholdsStyle": {"mode": "dashed"}}}, "overrides": []}},
    {"id": 7, "type": "timeseries", "title": "Worker utilization (%)", "gridPos": {"x": 12, "y": 91, "w": 12, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.pipeline AND logstash.pool:${pool:lucene} AND host.name:${host:lucene} AND logstash.pipeline.name:${pipeline:lucene}", "alias": "{{term host.name}} – {{term logstash.pipeline.name}}", "metrics": [{"id": "1", "type": "avg", "field": "logstash.pipeline.total.flow.worker_utilization.last_1_minute"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "3", "type": "terms", "field": "logstash.pipeline.name", "settings": {"size": "100", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto", "min_doc_count": "0"}}], "timeField": "@timestamp"}], "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}}, "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 80}]}, "custom": {"lineWidth": 1, "fillOpacity": 5, "showPoints": "never", "spanNulls": true, "thresholdsStyle": {"mode": "dashed"}}}, "overrides": []}},
    {"id": 103, "type": "row", "title": "Health", "gridPos": {"x": 0, "y": 99, "w": 24, "h": 1}, "collapsed": false, "panels": []},
    {"id": 8, "type": "table", "title": "Health report: latest state per pipeline (red = no measurement for more than 90 s)", "gridPos": {"x": 0, "y": 100, "w": 12, "h": 9}, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.health_report AND logstash.pipeline.id:* AND logstash.pool:${pool:lucene} AND logstash.node.name:${host:lucene} AND logstash.pipeline.id:${pipeline:lucene}", "metrics": [{"id": "1", "type": "max", "field": "@timestamp"}, {"id": "7", "type": "bucket_script", "pipelineVariables": [{"name": "latest", "pipelineAgg": "1"}], "settings": {"script": "double r = (${__to}L - params.latest) / 1000.0; return r > 90 ? Math.round(r) : 0;"}}], "bucketAggs": [{"id": "2", "type": "terms", "field": "logstash.node.name", "settings": {"size": "50", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "3", "type": "terms", "field": "logstash.pipeline.id", "settings": {"size": "500", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "terms", "field": "logstash.pipeline.status", "settings": {"size": "1", "order": "desc", "orderBy": "1", "min_doc_count": "1", "missing": "n/a"}}, {"id": "5", "type": "terms", "field": "logstash.pipeline.state", "settings": {"size": "1", "order": "desc", "orderBy": "1", "min_doc_count": "1", "missing": "n/a"}}, {"id": "6", "type": "terms", "field": "logstash.pipeline.symptom", "settings": {"size": "1", "order": "desc", "orderBy": "1", "min_doc_count": "1", "missing": "-"}}], "timeField": "@timestamp"}], "options": {"showHeader": true, "cellHeight": "sm"}, "fieldConfig": {"defaults": {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}}, "overrides": [{"matcher": {"id": "byName", "options": "Status"}, "properties": [{"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic"}}, {"id": "mappings", "value": [{"type": "value", "options": {"green": {"text": "green", "color": "green", "index": 0}, "yellow": {"text": "yellow", "color": "orange", "index": 1}, "red": {"text": "red", "color": "red", "index": 2}, "unknown": {"text": "unknown", "color": "text", "index": 3}, "n/a": {"text": "n/a", "color": "text", "index": 4}}}]}]}, {"matcher": {"id": "byName", "options": "No data for"}, "properties": [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 0}, {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "transparent", "value": null}, {"color": "red", "value": 90}]}}, {"id": "custom.cellOptions", "value": {"type": "color-background", "mode": "basic", "applyToRow": true}}, {"id": "mappings", "value": [{"type": "value", "options": {"0": {"text": "up to date", "index": 0}}}]}]}, {"matcher": {"id": "byName", "options": "Last measurement"}, "properties": [{"id": "unit", "value": "dateTimeAsLocal"}]}]}, "description": "Last known state of each pipeline (status, state and symptom of the most recent report). A pipeline that is stopped, has a configuration error or was removed no longer appears in the health report: its last state stays green, but its last measurement gets older. The \"No data for\" column compares this measurement with the dashboard end time (Elasticsearch bucket_script with ${__to}). Beyond 90 s (3 missed reports), the whole row turns red and moves to the top of the table.", "transformations": [{"id": "organize", "options": {"indexByName": {"logstash.node.name": 0, "logstash.pipeline.id": 1, "logstash.pipeline.status": 2, "logstash.pipeline.state": 3, "Bucket Script": 4, "Max": 5, "logstash.pipeline.symptom": 6}, "renameByName": {"logstash.node.name": "Node", "logstash.pipeline.id": "Pipeline", "logstash.pipeline.status": "Status", "logstash.pipeline.state": "State", "Bucket Script": "No data for", "Max": "Last measurement", "logstash.pipeline.symptom": "Symptom"}}}, {"id": "sortBy", "options": {"sort": [{"field": "Status", "desc": true}]}}, {"id": "sortBy", "options": {"sort": [{"field": "No data for", "desc": true}]}}]},
    {"id": 9, "type": "timeseries", "title": "Configuration reload failures (per interval)", "description": "Increase of the reloads.failures counter: configuration rejected by Logstash (the previous version keeps running, but will not restart).", "gridPos": {"x": 12, "y": 100, "w": 12, "h": 9}, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.pipeline AND logstash.pool:${pool:lucene} AND host.name:${host:lucene} AND logstash.pipeline.name:${pipeline:lucene}", "alias": "{{term host.name}} – {{term logstash.pipeline.name}}", "metrics": [{"id": "1", "type": "max", "field": "logstash.pipeline.total.reloads.failures", "hide": true}, {"id": "5", "type": "derivative", "field": "1", "settings": {}}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "3", "type": "terms", "field": "logstash.pipeline.name", "settings": {"size": "100", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "5m", "min_doc_count": "0"}}], "timeField": "@timestamp"}], "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["sum"]}, "tooltip": {"mode": "multi", "sort": "desc"}}, "fieldConfig": {"defaults": {"unit": "short", "min": 0, "decimals": 0, "custom": {"drawStyle": "bars", "fillOpacity": 80, "lineWidth": 1, "showPoints": "never"}}, "overrides": []}},
    {"id": 10, "type": "timeseries", "title": "JVM heap per node (%)", "gridPos": {"x": 0, "y": 109, "w": 24, "h": 7}, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.node AND logstash.pool:${pool:lucene} AND host.name:${host:lucene}", "alias": "{{term host.name}}", "metrics": [{"id": "1", "type": "max", "field": "logstash.node.stats.jvm.mem.heap_used_percent"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto", "min_doc_count": "0"}}], "timeField": "@timestamp"}], "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}}, "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "max": 100, "thresholds": {"mode": "absolute", "steps": [{"color": "green", "value": null}, {"color": "orange", "value": 90}]}, "custom": {"lineWidth": 1, "fillOpacity": 5, "showPoints": "never", "spanNulls": true, "thresholdsStyle": {"mode": "dashed"}}}, "overrides": []}},
    {"id": 104, "type": "row", "title": "Logstash process", "collapsed": false, "gridPos": {"x": 0, "y": 116, "w": 24, "h": 1}, "panels": []},
    {"id": 30, "type": "timeseries", "title": "Logstash process memory per node (RSS, JVM heap dashed)", "description": "RSS: memory actually used by the Logstash process (heap + off-heap + thread stacks + native buffers), measured by a System integration instance dedicated to the Logstash process only (metrics-system.process-logstash, no top N: always collected). Dashed: used JVM heap (Logstash API), which is only part of it (capped by -Xmx).", "gridPos": {"x": 0, "y": 117, "w": 12, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-system"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-system"}, "query": "data_stream.dataset:system.process AND data_stream.namespace:logstash AND logstash.pool:${pool:lucene} AND host.name:${host:lucene}", "alias": "{{term host.name}} – RSS", "metrics": [{"id": "1", "type": "max", "field": "system.process.memory.rss.bytes"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto", "min_doc_count": "0"}}], "timeField": "@timestamp"}, {"refId": "B", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.node AND logstash.pool:${pool:lucene} AND host.name:${host:lucene}", "alias": "{{term host.name}} – heap", "metrics": [{"id": "1", "type": "max", "field": "logstash.node.stats.jvm.mem.heap_used_in_bytes"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto", "min_doc_count": "0"}}], "timeField": "@timestamp"}], "fieldConfig": {"defaults": {"unit": "bytes", "min": 0, "custom": {"lineWidth": 1, "fillOpacity": 5, "showPoints": "never", "spanNulls": true}}, "overrides": [{"matcher": {"id": "byRegexp", "options": ".*– heap"}, "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [6, 4]}}, {"id": "custom.fillOpacity", "value": 0}]}]}, "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max"]}, "tooltip": {"mode": "multi", "sort": "desc"}}},
    {"id": 31, "type": "timeseries", "title": "Logstash process CPU per node (%)", "description": "CPU used by the Logstash process, as published by Logstash itself (API :9600, logstash.node.stats.process.cpu.percent, as % of the machine total capacity).", "gridPos": {"x": 12, "y": 117, "w": 12, "h": 8}, "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "targets": [{"refId": "A", "datasource": {"type": "elasticsearch", "uid": "es-logstash"}, "query": "data_stream.dataset:logstash.node AND logstash.pool:${pool:lucene} AND host.name:${host:lucene}", "alias": "{{term host.name}}", "metrics": [{"id": "1", "type": "max", "field": "logstash.node.stats.process.cpu.percent"}], "bucketAggs": [{"id": "2", "type": "terms", "field": "host.name", "settings": {"size": "20", "order": "asc", "orderBy": "_term", "min_doc_count": "1"}}, {"id": "4", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto", "min_doc_count": "0"}}], "timeField": "@timestamp"}], "fieldConfig": {"defaults": {"unit": "percent", "min": 0, "custom": {"lineWidth": 1, "fillOpacity": 5, "showPoints": "never", "spanNulls": true}}, "overrides": []}, "options": {"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["lastNotNull", "max", "mean"]}, "tooltip": {"mode": "multi", "sort": "desc"}}}
  ],
  "annotations": {"list": [{"name": "P1 alerts (fired)", "enable": true, "iconColor": "red", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "status:firing AND priority:P1", "timeField": "time", "textField": "problem", "tagsField": "host", "target": {"refId": "Anno", "query": "status:firing AND priority:P1", "limit": 200}}, {"name": "P2 alerts (fired)", "enable": true, "iconColor": "orange", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "status:firing AND priority:P2", "timeField": "time", "textField": "problem", "tagsField": "host", "target": {"refId": "Anno", "query": "status:firing AND priority:P2", "limit": 200}}, {"name": "Recovered alerts", "enable": false, "iconColor": "green", "datasource": {"type": "elasticsearch", "uid": "es-alerts-elklab"}, "query": "status:recovered", "timeField": "time", "textField": "rule", "tagsField": "host", "target": {"refId": "Anno", "query": "status:recovered", "limit": 200}}]}
}
```

## 6. Vérification

### 6.1 Après déploiement

```
GET kbn:/api/alerting/rules/_find?per_page=100&filter=alert.attributes.tags:logstash&fields=name,enabled,execution_status

POST _query?format=txt
{"query": "FROM alerts-elklab | STATS n = COUNT(*), latest = MAX(time) BY rule, status | SORT latest DESC"}
```

Attendu : 14 règles, `enabled: true`, `execution_status.status` = `ok` (ou `active` si une anomalie existe).
Dans Grafana : panel *Alert rule health* entièrement `OK`, *Data source freshness* sans `SILENT`.

### 6.2 Tests par règle

Hors production, sur un nœud dont le trafic a été retiré. Chaque test doit produire un document `firing` dans
`alerts-elklab` (et dans *Active alerts*) après la fenêtre et les confirmations de la règle, puis `recovered` une fois
la cause retirée. Pour les règles basées sur les logs, écrire les lignes de test dans un **fichier séparé**
`/DATA/logstash/logs/logstash-plain-test.log` (Logstash réécrit `logstash-plain.log`), au format
`[2026-01-01T00:00:00,000][ERROR][logstash.runner ] TEST java.lang.OutOfMemoryError`.

| Règle | Test |
|---|---|
| Logstash – process not running | Arrêter Logstash sur un nœud |
| Logstash – restart loop | 3 démarrages en moins de 10 min |
| Logstash – critical JVM heap (> 95 % over 10 min) | Réduire `-Xmx` sur un nœud de test chargé |
| Logstash – OutOfMemoryError | Ligne de test dans un fichier `logstash-plain-test.log` |
| Logstash – pool with no healthy node | Arrêter Logstash sur tous les nœuds d'un pool de test |
| Logstash – Elasticsearch unreachable | Couper le flux réseau vers Elasticsearch depuis un nœud |
| Logstash – node unreachable (ping) | Bloquer l'ICMP vers un nœud (pare-feu) |
| Logstash – node disk usage (/ and /DATA) | `fallocate -l <taille> /DATA/test-alerte.tmp` au-delà de 80 %, puis supprimer |
| Logstash – monitoring data missing (node) | `systemctl stop elastic-agent` sur un nœud |
| Logstash pipeline – not running | Bloquer l'écriture de l'index de sortie : `PUT <index>/_settings {"index.blocks.write": true}`, puis `null` |
| Logstash pipeline – input port closed (Osquery) | Ajouter un port fictif dans `VALUES` de la requête du pool, puis le retirer |
| Logstash pipeline – queue at risk | Bloquer l'écriture de l'index de sortie d'un pipeline à file persistante |
| Logstash pipeline – data loss (events rejected by Elasticsearch) | Envoyer un événement dont un champ contredit le mapping de l'index cible |
| Logstash pipeline – critical pipeline throughput | Monter `min_eps` d'un pipeline au-dessus de son débit réel, puis remettre la valeur |

Nettoyage des documents de test (les tests écrivent sous les vrais identifiants de règle) :

```
POST alerts-elklab/_delete_by_query?refresh=true
{"query": {"range": {"time": {"gte": "<début des tests>", "lte": "<fin des tests>"}}}}
```

## 7. Retour arrière

Dans l'ordre :

```
DELETE kbn:/api/alerting/rule/<ID>                  # chacune des 14 règles
DELETE kbn:/api/osquery/packs/<ID_PACK>             # GET kbn:/api/osquery/packs
DELETE kbn:/api/fleet/package_policies/<ID>         # instance process Logstash, intégrations Osquery
DELETE kbn:/api/synthetics/monitors/<ID>            # moniteurs ping
DELETE kbn:/api/data_views/data_view/lab-dv-system-filesystem
DELETE kbn:/api/actions/connector/alerts-elklab-index
DELETE logstash-pipelines-critiques
DELETE _index_template/alerts-elklab
DELETE alerts-elklab-*
DELETE _ilm/policy/alerts-elklab
DELETE _security/user/grafana_reader
DELETE _security/role/grafana_reader
```

Grafana : supprimer le dashboard et les 4 datasources. Le champ `logstash.pool` reste mappé dans les index existants.

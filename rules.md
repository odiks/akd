# Mise en production de l'alerting Logstash P1

Procédure pas à pas pour déployer, dans un cluster Elastic **9.5.x** et un Grafana **13**, l'alerting P1 des
nœuds Logstash mis au point dans le lab :

- **Kibana** : règles d'alerte P1 qui écrivent chaque déclenchement et chaque levée dans un **journal d'alertes**
  (index `alerts-elklab`) avec un message lisible ;
- **Grafana** : deux panels, *Alertes en cours* et *Historique des alertes*, qui lisent ce journal.

Deux variantes de collecte, à choisir **par pool de Logstash** :

| | Partie A – Elastic Agent (Fleet) | Partie B – Beats (Metricbeat, Filebeat) |
|---|---|---|
| Collecte | Intégrations System, Logstash, Osquery Manager, Synthetics | Metricbeat (`system`, `logstash` en mode xpack), Filebeat (`logstash`), pipeline Logstash d'aplatissement par nœud |
| Règles P1 | 14 (+1 optionnelle) | 11 (+1 optionnelle), préfixées `[BEATS]` |
| Non couvert | — | Ports par Osquery (pas d'Osquery sans Elastic Agent) |

Tout se fait par copier-coller : requêtes **Kibana Dev Tools** (Console) et **JSON** pour Grafana. Aucun script.

> Dans Dev Tools, une requête qui commence par `kbn:` est envoyée à l'API **Kibana** (règles, connecteurs,
> Osquery, Synthetics) ; les autres partent vers **Elasticsearch**.

---

## 0. Avant de commencer

### 0.1 Prérequis

| Élément | Requis |
|---|---|
| Versions | Elasticsearch / Kibana 9.5.x (ES\|QL avec `INLINE STATS`, `QSTR`, `LOOKUP JOIN`) ; Grafana 13 (requêtes ES\|QL dans la datasource Elasticsearch) |
| Licence | Gestion centralisée des pipelines Logstash : Enterprise. Règles ES\|QL et connecteur index : licence de base |
| Droits | Compte avec `manage_security`, `manage_index_templates`, `manage_ilm` (Elasticsearch) et les droits *Rules*, *Connectors*, *Fleet*, *Osquery*, *Synthetics* (Kibana) |
| Logstash | Installation dans `/DATA/logstash`, API locale sur `127.0.0.1:9600`, nœuds nommés `logstash-*` |
| Pools | Chaque nœud porte son pool dans le champ `logstash.pool` (`pool1`, `pool2`…) |

### 0.2 Ce qu'il faut adapter

| Valeur dans cette procédure | À remplacer par |
|---|---|
| `logstash-*` (filtres `host.name LIKE "logstash-*"`) | Le motif de nom de vos nœuds Logstash |
| `pool2` dans les règles Elastic Agent `process absent`, `pool sans nœud sain` | Les pools surveillés par les **Beats** (exclus des règles Agent pour éviter de fausses alertes). Supprimer la condition si aucun pool n'est en Beats |
| `https://es01…:9200` | Vos nœuds Elasticsearch |
| `<…>` | Valeurs propres à votre environnement (mots de passe, ID d'emplacement Synthetics, policies) |

### 0.3 Conventions

- **Nom** : `[P1] Logstash – <problème>` ; préfixe `[BEATS]` pour les règles qui lisent les données Beats.
- **Tags** : `logstash`, `P1`, `elk-lab` (à remplacer par le tag de votre périmètre), `beats` pour la partie B.
- **Une alerte par ligne** de résultat ES\|QL (`groupBy: row`) : par nœud, par nœud et pipeline, ou par pool.
- **Journal** : un document `firing` à chaque déclenchement, un document `recovered` à la levée.

---

## 1. Socle commun (une fois par cluster)

### 1.1 Rôle de lecture pour Grafana

```
PUT _security/role/grafana_reader
{
  "cluster": [
    "monitor"
  ],
  "indices": [
    {
      "names": [
        ".alerts-*",
        ".internal.alerts-*",
        "alerts-elklab*"
      ],
      "privileges": [
        "read",
        "view_index_metadata"
      ]
    },
    {
      "names": [
        "metrics-*",
        "logs-*"
      ],
      "privileges": [
        "read",
        "view_index_metadata"
      ]
    }
  ]
}

PUT _security/user/grafana_reader
{
  "password": "<MOT_DE_PASSE_GRAFANA>",
  "roles": ["grafana_reader"],
  "full_name": "Grafana - lecture des alertes et métriques"
}
```

### 1.2 Cycle de vie du journal d'alertes (ILM)

Rotation tous les 30 jours ou à 10 Go, suppression après 1 an (à adapter à votre politique de conservation).

```
PUT _ilm/policy/alerts-elklab
{
  "policy": {
    "phases": {
      "hot":    {"actions": {"rollover": {"max_age": "30d", "max_primary_shard_size": "10gb"}}},
      "delete": {"min_age": "365d", "actions": {"delete": {}}}
    }
  }
}
```

### 1.3 Modèle d'index et mapping

Mapping **strict** : tout champ imprévu est refusé, ce qui garantit un journal propre et exploitable par
n'importe quel outil.

| Champ | Type | Contenu |
|---|---|---|
| `host` | keyword | Nœud (ou pool, ou liste de nœuds pour les règles de pool) |
| `pipeline` | keyword | Pipeline concerné, si la règle en a un |
| `severity` | keyword | `critical` ; `ok` à la levée |
| `priority` | keyword | `P1` |
| `status` | keyword | `firing` / `recovered` |
| `problem` | text + keyword | Message lisible, propre à chaque règle |
| `time`, `@timestamp` | date | Heure de l'événement |
| `rule`, `rule_id`, `alert_id` | keyword | Règle et identifiant de l'alerte (relie une levée à son déclenchement) |

```
PUT _index_template/alerts-elklab
{
  "index_patterns": [
    "alerts-elklab-*"
  ],
  "priority": 500,
  "_meta": {
    "description": "Journal des alertes Kibana : un document par déclenchement et par levée"
  },
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 1,
      "index.lifecycle.name": "alerts-elklab",
      "index.lifecycle.rollover_alias": "alerts-elklab"
    },
    "mappings": {
      "dynamic": "strict",
      "properties": {
        "@timestamp": {
          "type": "date"
        },
        "time": {
          "type": "date"
        },
        "host": {
          "type": "keyword"
        },
        "pipeline": {
          "type": "keyword"
        },
        "severity": {
          "type": "keyword"
        },
        "priority": {
          "type": "keyword"
        },
        "status": {
          "type": "keyword"
        },
        "problem": {
          "type": "text",
          "fields": {
            "keyword": {
              "type": "keyword",
              "ignore_above": 1024
            }
          }
        },
        "rule": {
          "type": "keyword"
        },
        "rule_id": {
          "type": "keyword"
        },
        "alert_id": {
          "type": "keyword"
        }
      }
    }
  }
}
```

### 1.4 Premier index et alias d'écriture

Les règles écrivent dans l'**alias** `alerts-elklab` ; ILM crée les index suivants (`alerts-elklab-000002`…).

```
PUT alerts-elklab-000001
{
  "aliases": {"alerts-elklab": {"is_write_index": true}}
}
```

Vérification : `GET alerts-elklab/_mapping` doit montrer `"dynamic": "strict"` et les 11 champs.

### 1.5 Connecteur des règles

Toutes les règles de cette procédure utilisent ce connecteur (ID `alerts-elklab-index`).

```
POST kbn:/api/actions/connector/alerts-elklab-index
{
  "connector_type_id": ".index",
  "name": "Journal des alertes Logstash (alerts-elklab)",
  "config": {
    "index": "alerts-elklab",
    "refresh": true,
    "executionTimeField": "@timestamp"
  }
}
```

Test d'écriture (puis suppression du document de test) :

```
POST kbn:/api/actions/connector/alerts-elklab-index/_execute
{
  "params": {"documents": [{"rule_id": "TEST", "rule": "Test du connecteur", "alert_id": "test", "host": "logstash-test",
    "pipeline": "", "severity": "ok", "priority": "P1", "status": "recovered", "problem": "Test d'écriture", "time": "2026-01-01T00:00:00Z"}]}
}

GET alerts-elklab/_search?q=rule_id:TEST

POST alerts-elklab/_delete_by_query?refresh=true
{"query": {"term": {"rule_id": "TEST"}}}
```

---

## 2. Anatomie d'une règle : exemple commenté

Toutes les règles P1 sont des règles **Elasticsearch query en ES\|QL**. Exemple : *process absent*.

| Champ | Valeur | Rôle |
|---|---|---|
| `rule_type_id` / `consumer` | `.es-query` / `stackAlerts` | Règle « Elasticsearch query » ; ses alertes sont visibles dans *Stack Management → Alerts* |
| `schedule.interval` | `1m` | Exécution chaque minute |
| `alert_delay.active` | `2` | Alerte créée seulement après **2 exécutions consécutives** en anomalie (évite les faux positifs ponctuels) |
| `params.searchType` | `esqlQuery` | Requête ES\|QL |
| `params.timeWindowSize/Unit` | `60 m` | Fenêtre de données lue à chaque exécution |
| `params.threshold` / `thresholdComparator` | `[0]` / `>` | Alerte dès que la requête renvoie **au moins une ligne** |
| `params.groupBy` | `row` | **Une alerte par ligne** ; son identifiant = valeurs du `STATS … BY` |
| `actions` (groupe `query matched`) | document `firing` | Valeurs de la ligne : `{{#context.hits}}{{_source.<colonne>}}{{/context.hits}}` |
| `actions` (groupe `recovered`) | document `recovered` | Les valeurs de la ligne sont vides à la levée : le nœud vient de `{{alert.id}}` |

Requête de *process absent*, commentée :

```esql
FROM metrics-system.*                                         // métriques système des nœuds
| WHERE host.name LIKE "logstash-*" AND (logstash.pool IS NULL OR logstash.pool != "pool2")
| INLINE STATS anchor = MAX(@timestamp)                       // donnée la plus récente de TOUS les nœuds Logstash
| WHERE data_stream.dataset == "system.process"
| EVAL ls_ts = CASE(process.command_line LIKE "*org.logstash.Logstash*", @timestamp, NULL)
| STATS last_ls = MAX(ls_ts), anchor = MAX(anchor) BY host.name   // dernier process Logstash vu, par nœud
| EVAL retard_s = DATE_DIFF("second", COALESCE(last_ls, NOW() - 1 hour), anchor)
| WHERE retard_s > 120                                        // absent depuis plus de 2 min
| EVAL dernier_vu = COALESCE(TO_STRING(last_ls), "aucun dans la dernière heure")
| KEEP host.name, last_ls, dernier_vu, retard_s
```

Le retard est mesuré par rapport aux **autres nœuds** (`anchor`) et non à l'heure courante : un retard global
d'ingestion ne déclenche pas de fausse alerte.

**Création par l'interface** (équivalent) : *Stack Management → Rules → Create rule → Elasticsearch query →
ES\|QL*, coller la requête, *Time window* = 60 min, *Alert group* = « Create an alert for each row »,
*Advanced options → Alert delay* = 2, puis une action **Index** (`alerts-elklab-index`) par groupe (*Query matched*,
*Recovered*) avec le document JSON du bloc `actions` ci-dessous.

> Pièges ES\|QL (éditeur Kibana) : pas de `LIKE` ni de `CASE` **à l'intérieur** d'un `STATS` (calculer
> dans un `EVAL` avant) ; `last` est un mot réservé.

---

## 3. Partie A – nœuds surveillés par Elastic Agent

### 3.1 Collecte (Fleet)

| Élément | Réglage |
|---|---|
| Agent policy | Une par pool, avec la **balise globale** `logstash.pool` = nom du pool (*Agent policy → Advanced → Custom fields*) |
| Intégration **System** | Par défaut (process, filesystem, CPU, mémoire) |
| Intégration **Logstash** | Logs : `/DATA/logstash/logs/logstash-plain*.log` et `/DATA/logstash/logs/logstash-slowlog-plain*.log` ; métriques : `http://127.0.0.1:9600` |
| Intégration **Osquery Manager** | **Dédiée** aux policies Logstash (un pack assigné à une intégration partagée tourne sur tous ses agents) |
| **Synthetics** | Un emplacement privé (agent Docker, image `elastic-agent-complete`) relié à sa propre policy |

Le champ `logstash.pool` doit être mappé (les data streams Logstash ont `dynamic: false`) :

```
PUT _component_template/logstash@custom
{
  "template": {"mappings": {"properties": {"logstash": {"properties": {"pool": {"type": "keyword"}}}}}}
}
```

Puis, pour les data streams déjà existants :

```
PUT metrics-logstash.*,logs-logstash.*/_mapping?allow_no_indices=true
{"properties": {"logstash": {"properties": {"pool": {"type": "keyword"}}}}}
```

### 3.2 Osquery : pack des ports et inventaire attendu (règle *port fermé sur tout le pool*)

Pack `logstash-ports` : ports ouverts par le JVM Logstash (hors boucle locale) + une ligne **heartbeat** pour
qu'une exécution sans port reste visible.

```
POST kbn:/api/osquery/packs
{
  "name": "logstash-ports",
  "description": "Ports réseau ouverts par le process Logstash (hors boucle locale), plus une ligne heartbeat (transport=heartbeat, logstash_running = nombre de process Logstash) pour que chaque exécution produise au moins une ligne. Snapshot : comparaison avec l inventaire attendu. Différentiel : fermeture détectée en moins de 2 min.",
  "enabled": true,
  "policy_ids": [
    "<POLICY_POOL_1>",
    "<POLICY_POOL_2>"
  ],
  "queries": {
    "logstash_ports_snapshot": {
      "query": "SELECT lp.port AS port, CASE lp.protocol WHEN 6 THEN 'tcp' WHEN 17 THEN 'udp' ELSE CAST(lp.protocol AS TEXT) END AS transport, lp.address AS address, p.name AS name, 1 AS logstash_running FROM listening_ports lp JOIN processes p USING (pid) WHERE p.cmdline LIKE '%org.logstash.Logstash%' AND lp.address NOT IN ('127.0.0.1', '::1', '::ffff:127.0.0.1') UNION ALL SELECT 0, 'heartbeat', '', 'osquery', (SELECT COUNT(*) FROM processes WHERE cmdline LIKE '%org.logstash.Logstash%');",
      "interval": 120,
      "snapshot": true,
      "removed": false,
      "ecs_mapping": {
        "server.port": {
          "field": "port"
        },
        "network.transport": {
          "field": "transport"
        },
        "server.address": {
          "field": "address"
        },
        "process.name": {
          "field": "name"
        }
      }
    },
    "logstash_ports_diff": {
      "query": "SELECT lp.port AS port, CASE lp.protocol WHEN 6 THEN 'tcp' WHEN 17 THEN 'udp' ELSE CAST(lp.protocol AS TEXT) END AS transport, lp.address AS address, p.name AS name, 1 AS logstash_running FROM listening_ports lp JOIN processes p USING (pid) WHERE p.cmdline LIKE '%org.logstash.Logstash%' AND lp.address NOT IN ('127.0.0.1', '::1', '::ffff:127.0.0.1') UNION ALL SELECT 0, 'heartbeat', '', 'osquery', (SELECT COUNT(*) FROM processes WHERE cmdline LIKE '%org.logstash.Logstash%');",
      "interval": 60,
      "snapshot": false,
      "removed": true,
      "ecs_mapping": {
        "server.port": {
          "field": "port"
        },
        "network.transport": {
          "field": "transport"
        },
        "server.address": {
          "field": "address"
        },
        "process.name": {
          "field": "name"
        }
      }
    }
  }
}
```

Inventaire des ports attendus par pool (lookup index) :

```
PUT logstash-ports-attendus
{
  "settings": {"index.mode": "lookup"},
  "mappings": {"properties": {
    "logstash": {"properties": {"pool": {"type": "keyword"}, "pipeline": {"properties": {"name": {"type": "keyword"}}}}},
    "network": {"properties": {"transport": {"type": "keyword"}}},
    "server": {"properties": {"port": {"type": "long"}}},
    "priorite": {"type": "keyword"},
    "cle": {"type": "keyword"}
  }}
}

POST logstash-ports-attendus/_bulk?refresh=true
{"index":{}}
{"logstash": {"pool": "pool1", "pipeline": {"name": "pool1-syslog_in"}}, "network": {"transport": "tcp"}, "server": {"port": 5514}, "priorite": "P1", "cle": "pool1|tcp|5514"}
{"index":{}}
{"logstash": {"pool": "pool1", "pipeline": {"name": "pool1-syslog_in"}}, "network": {"transport": "udp"}, "server": {"port": 5514}, "priorite": "P1", "cle": "pool1|udp|5514"}
{"index":{}}
{"logstash": {"pool": "pool2", "pipeline": {"name": "pool2-webhook_in"}}, "network": {"transport": "tcp"}, "server": {"port": 8080}, "priorite": "P1", "cle": "pool2|tcp|8080"}
```

Une ligne par **pool × pipeline exposé × transport × port**. À tenir à jour à chaque nouveau pipeline `_in`.

### 3.3 Synthetics : moniteurs des inputs et ping

Emplacement privé (ID à reporter dans les moniteurs) :

```
POST kbn:/api/synthetics/private_locations
{"label": "<NOM_EMPLACEMENT>", "agentPolicyId": "<POLICY_SYNTHETICS>"}
```

Un moniteur par input réseau et par nœud. **Convention de nom obligatoire** `<pipeline> · <nœud> · <proto>/<port>`
et **tag `input`** (la règle en dépend) ; un moniteur ICMP `ping · <nœud>` avec le tag `ping`.

```
POST kbn:/api/synthetics/monitors
{
  "type": "tcp",
  "name": "pool1-syslog_in · logstash-1 · tcp/5514",
  "host": "logstash-1.orb.local:5514",
  "schedule": 1,
  "timeout": "10",
  "ipv4": true,
  "ipv6": false,
  "tags": [
    "input",
    "pool1",
    "pool1-syslog_in",
    "logstash-1",
    "elk-lab"
  ],
  "enabled": true,
  "private_locations": [
    "<ID_EMPLACEMENT>"
  ]
}

POST kbn:/api/synthetics/monitors
{
  "type": "http",
  "name": "pool2-webhook_in · logstash-3 · http/8080",
  "url": "http://logstash-3.orb.local:8080/",
  "schedule": 1,
  "timeout": "10",
  "ipv4": true,
  "ipv6": false,
  "check.response.status": [
    "401"
  ],
  "response.include_body": "never",
  "max_redirects": "0",
  "tags": [
    "input",
    "pool2",
    "pool2-webhook_in",
    "logstash-3",
    "elk-lab"
  ],
  "enabled": true,
  "private_locations": [
    "<ID_EMPLACEMENT>"
  ]
}

POST kbn:/api/synthetics/monitors
{
  "type": "icmp",
  "name": "ping · logstash-1",
  "host": "logstash-1.orb.local",
  "schedule": 1,
  "wait": "1",
  "timeout": "10",
  "ipv4": true,
  "ipv6": false,
  "tags": [
    "ping",
    "pool1",
    "logstash-1",
    "elk-lab"
  ],
  "enabled": true,
  "private_locations": [
    "<ID_EMPLACEMENT>"
  ]
}
```

- HTTP : moniteur **sans identifiants**, réponse attendue **401** (l'input répond et l'authentification est
  active, sans créer d'événement).
- `ipv6: false` si le réseau de l'emplacement privé n'a pas d'IPv6.
- Désactiver les règles par défaut de Synthetics (*Synthetics → Settings → Alerting*) pour éviter les doublons.

### 3.4 Règles P1 (Elastic Agent)

#### `kb-ls-p1-process-absent` – [P1] Logstash – process absent

Process `org.logstash.Logstash` non vu depuis plus de 120 s sur un nœud, alors que les autres nœuds Logstash remontent encore leurs métriques (si l'agent du nœud est muet, l'alerte se déclenche aussi). Source : `metrics-system.*`.

```
POST kbn:/api/alerting/rule/kb-ls-p1-process-absent
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
      "esql": "FROM metrics-system.* | WHERE host.name LIKE \"logstash-*\" AND (logstash.pool IS NULL OR logstash.pool != \"pool2\") | INLINE STATS anchor = MAX(@timestamp) | WHERE data_stream.dataset == \"system.process\" | EVAL ls_ts = CASE(process.command_line LIKE \"*org.logstash.Logstash*\", @timestamp, NULL) | STATS last_ls = MAX(ls_ts), anchor = MAX(anchor) BY host.name | EVAL retard_s = DATE_DIFF(\"second\", COALESCE(last_ls, NOW() - 1 hour), anchor) | WHERE retard_s > 120 | EVAL dernier_vu = COALESCE(TO_STRING(last_ls), \"aucun dans la dernière heure\") | KEEP host.name, last_ls, dernier_vu, retard_s"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-process-absent",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Process Logstash absent depuis {{#context.hits}}{{_source.retard_s}}{{/context.hits}} s (dernier vu : {{#context.hits}}{{_source.dernier_vu}}{{/context.hits}}). Vérifier : sudo startlogstash status, /DATA/logstash/logs/logstash-plain.log",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-process-absent",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Process Logstash de nouveau présent",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-ls-p1-restart-loop` – [P1] Logstash – démarrages en boucle

Plus de 2 « Starting Logstash » en 10 min sur un nœud : crash ou redémarrage en boucle. Source : `logs-logstash.log-*`.

```
POST kbn:/api/alerting/rule/kb-ls-p1-restart-loop
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-restart-loop",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "{{#context.hits}}{{_source.demarrages}}{{/context.hits}} démarrages de Logstash en 10 min : crash ou redémarrage en boucle. Voir le début de /DATA/logstash/logs/logstash-plain.log et logstash-stdout.log",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-restart-loop",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Plus de démarrage en boucle (moins de 3 démarrages sur 10 min)",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-ls-p1-heap` – [P1] Logstash – heap JVM critique (> 95 % sur 10 min)

Heap JVM moyen > 95 % sur 10 min. Source : `metrics-logstash.node-*`.

```
POST kbn:/api/alerting/rule/kb-ls-p1-heap
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-heap",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Heap JVM à {{#context.hits}}{{_source.heap_pct}}{{/context.hits}} % en moyenne sur 10 min : GC permanent, OutOfMemoryError probable. Augmenter le heap (config/jvm.options) ou réduire la charge.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-heap",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Heap JVM revenu sous 95 %",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-ls-p1-oom` – [P1] Logstash – OutOfMemoryError

`OutOfMemoryError` dans les logs Logstash (5 min). Source : `logs-logstash.log-*`.

```
POST kbn:/api/alerting/rule/kb-ls-p1-oom
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-oom",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "OutOfMemoryError dans les logs Logstash ({{#context.hits}}{{_source.occurrences}}{{/context.hits}} occurrence(s) en 5 min) : le process est probablement instable ou arrêté. Vérifier sudo startlogstash status.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-oom",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Plus d'OutOfMemoryError dans les logs",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-ls-p1-pipeline-not-running` – [P1] Logstash – pipeline arrêté ou en erreur (health report)

Health report : pipeline `FINISHED`, `TERMINATED` ou `LOADING`, ou statut `red` (fenêtre 3 min, 3 confirmations). `UNKNOWN` exclu (pipelines supprimés). Source : `metrics-logstash.health_report-*`.

```
POST kbn:/api/alerting/rule/kb-ls-p1-pipeline-not-running
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-pipeline-not-running",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Pipeline dans l'état {{#context.hits}}{{_source.etat}}{{/context.hits}} (statut {{#context.hits}}{{_source.statut}}{{/context.hits}}) d'après le health report Logstash, depuis plus de 2 min.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-pipeline-not-running",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Pipeline de nouveau RUNNING",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-ls-p1-shutdown-stalled` – [P1] Logstash – arrêt de pipeline figé

« shutdown process appears to be stalled » : arrêt de pipeline figé. Source : `logs-logstash.log-*`.

```
POST kbn:/api/alerting/rule/kb-ls-p1-shutdown-stalled
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-shutdown-stalled",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Arrêt de pipeline figé (shutdown stalled, {{#context.hits}}{{_source.occurrences}}{{/context.hits}} fois en 5 min) : workers bloqués, typiquement sur une sortie qui réessaie. Le pipeline est injoignable pendant ce temps.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-shutdown-stalled",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Plus d'arrêt de pipeline figé",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-ls-p1-pool-down` – [P1] Logstash – pool sans aucun nœud sain

Aucun nœud d'un pool ne remonte de métriques de pipeline depuis plus de 180 s (par rapport aux autres nœuds). Source : `metrics-logstash.pipeline-*,metrics-system.*`.

```
POST kbn:/api/alerting/rule/kb-ls-p1-pool-down
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
      "esql": "FROM metrics-logstash.pipeline-*,metrics-system.* | WHERE host.name LIKE \"logstash-*\" AND (logstash.pool IS NULL OR logstash.pool != \"pool2\") | INLINE STATS anchor = MAX(@timestamp) | WHERE data_stream.dataset == \"logstash.pipeline\" | STATS last_seen = MAX(@timestamp), anchor = MAX(anchor) BY logstash.pool | EVAL retard_s = DATE_DIFF(\"second\", last_seen, anchor) | WHERE retard_s > 180 | KEEP logstash.pool, last_seen, retard_s"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-pool-down",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.logstash.pool}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Aucun nœud du pool {{#context.hits}}{{_source.logstash.pool}}{{/context.hits}} ne remonte de métriques de pipeline depuis {{#context.hits}}{{_source.retard_s}}{{/context.hits}} s (dernière : {{#context.hits}}{{_source.last_seen}}{{/context.hits}}) : tous les flux du pool sont interrompus.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-pool-down",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Le pool remonte de nouveau des métriques",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-ls-p1-p2p-broken` – [P1] Logstash – chaîne _in → _out coupée

Plus de 30 « address was unavailable » en 2 min : le pipeline `_in` n'atteint plus son `_out`. Source : `logs-logstash.log-*`.

```
POST kbn:/api/alerting/rule/kb-ls-p1-p2p-broken
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-p2p-broken",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Le pipeline _in n'arrive plus à envoyer vers son pipeline _out : {{#context.hits}}{{_source.tentatives}}{{/context.hits}} tentatives en 2 min (destination arrêtée, figée ou en rechargement). L'input est bloqué.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-p2p-broken",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Chaîne _in → _out rétablie",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-ls-p1-es-unreachable` – [P1] Logstash – Elasticsearch injoignable

« Elasticsearch Unreachable » ou « Marking url as dead » : Logstash n'atteint plus Elasticsearch. Source : `logs-logstash.log-*`.

```
POST kbn:/api/alerting/rule/kb-ls-p1-es-unreachable
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-es-unreachable",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Elasticsearch injoignable depuis Logstash : {{#context.hits}}{{_source.erreurs}}{{/context.hits}} erreur(s) en 2 min (Elasticsearch Unreachable / Marking url as dead). Les files persistantes vont se remplir, puis les inputs se bloquer.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-es-unreachable",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Logstash joint de nouveau Elasticsearch",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-ls-p1-index-rejected` – [P1] Logstash – événements rejetés par Elasticsearch (perte de données)

« Could not index event » : documents rejetés définitivement (perte sans DLQ). Source : `logs-logstash.log-*`.

```
POST kbn:/api/alerting/rule/kb-ls-p1-index-rejected
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-index-rejected",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "{{#context.hits}}{{_source.rejets}}{{/context.hits}} événement(s) rejeté(s) définitivement par Elasticsearch en 5 min (conflit de mapping, erreur 400) : perte de données sans file d'événements rejetés (DLQ).",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-index-rejected",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Plus d'événement rejeté par Elasticsearch",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-ls-p1-port-in-use` – [P1] Logstash – port d’écoute déjà utilisé

« Address already in use » : un input n'a pas pu ouvrir son port. Source : `logs-logstash.log-*`.

```
POST kbn:/api/alerting/rule/kb-ls-p1-port-in-use
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-port-in-use",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Un input n'a pas pu ouvrir son port (Address already in use, {{#context.hits}}{{_source.occurrences}}{{/context.hits}} fois en 5 min) : le pipeline ne démarre pas.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-ls-p1-port-in-use",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Plus d'erreur de port déjà utilisé",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-osq-p1-port-absent-pool` – [P1] Logstash – port d’input fermé sur tout le pool (Osquery)

Osquery : un port attendu (inventaire) n'est ouvert sur aucun nœud actif du pool. Source : `logs-osquery_manager.result-*`.

```
POST kbn:/api/alerting/rule/kb-osq-p1-port-absent-pool
{
  "name": "[P1] Logstash – port d’input fermé sur tout le pool (Osquery)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "osquery",
    "ports",
    "P1",
    "elk-lab"
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
      "esql": "FROM logs-osquery_manager.result-* | WHERE query_name == \"logstash_ports_snapshot\" AND logstash.pool IS NOT NULL | INLINE STATS dernier_run = MAX(osquery_meta.planned_schedule_time) BY host.name | WHERE osquery_meta.planned_schedule_time == dernier_run | EVAL cle_obs = CONCAT(network.transport, \"/\", TO_STRING(server.port)), ls_up = CASE(network.transport == \"heartbeat\" AND osquery.logstash_running != 0, 1, 0) | STATS ports_ouverts = VALUES(cle_obs), logstash_actif = MAX(ls_up), dernier_snapshot = MAX(@timestamp) BY host.name, logstash.pool | WHERE logstash_actif == 1 | LOOKUP JOIN logstash-ports-attendus ON logstash.pool | WHERE logstash.pipeline.name IS NOT NULL | EVAL attendu = CONCAT(network.transport, \"/\", TO_STRING(server.port)), absent = CASE(MV_CONTAINS(ports_ouverts, attendu), 0, 1) | INLINE STATS noeuds_pool = COUNT_DISTINCT(host.name) BY logstash.pool | WHERE absent == 1 | STATS noeuds_ko = COUNT_DISTINCT(host.name), noeuds = VALUES(host.name), noeuds_pool = MAX(noeuds_pool), dernier_snapshot = MAX(dernier_snapshot) BY logstash.pool, logstash.pipeline.name, port = attendu, priorite | WHERE noeuds_ko == noeuds_pool | KEEP logstash.pool, logstash.pipeline.name, port, noeuds_ko, noeuds_pool, noeuds, dernier_snapshot"
    },
    "timeField": "@timestamp",
    "timeWindowSize": 6,
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-osq-p1-port-absent-pool",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.noeuds}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Le port {{#context.hits}}{{_source.port}}{{/context.hits}} n'est ouvert sur aucun des {{#context.hits}}{{_source.noeuds_pool}}{{/context.hits}} nœud(s) actifs du pool {{#context.hits}}{{_source.logstash.pool}}{{/context.hits}} ({{#context.hits}}{{_source.noeuds}}{{/context.hits}}) : le flux est coupé. Vérifier le pipeline (health report, logs Address already in use).",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-osq-p1-port-absent-pool",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Port de nouveau ouvert sur le pool",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-synth-p1-input-down` – [P1] Logstash – input réseau injoignable (Synthetics)

Synthetics : toutes les vérifications TCP/HTTP d'un input des 3 dernières minutes en échec. Source : `synthetics-tcp-*,synthetics-http-*`.

```
POST kbn:/api/alerting/rule/kb-synth-p1-input-down
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-synth-p1-input-down",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "{{#context.hits}}{{_source.cible}}{{/context.hits}} ne répond plus depuis l'emplacement {{#context.hits}}{{_source.observer.geo.name}}{{/context.hits}} : {{#context.hits}}{{_source.echecs}}{{/context.hits}}/{{#context.hits}}{{_source.verifs}}{{/context.hits}} vérifications en échec sur 3 min. Erreur : {{#context.hits}}{{_source.erreur}}{{/context.hits}}. Dernier succès : {{#context.hits}}{{_source.dernier_succes}}{{/context.hits}}.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-synth-p1-input-down",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Input de nouveau joignable",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `kb-synth-p1-ping-down` – [P1] Logstash – nœud injoignable (ping)

Synthetics : tous les pings d'un nœud des 3 dernières minutes en échec. Source : `synthetics-icmp-*`.

```
POST kbn:/api/alerting/rule/kb-synth-p1-ping-down
{
  "name": "[P1] Logstash – nœud injoignable (ping)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "synthetics",
    "ping",
    "P1",
    "elk-lab"
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
      "esql": "FROM synthetics-icmp-* | WHERE summary.up IS NOT NULL AND QSTR(\"tags:ping\") | DISSECT monitor.name \"ping · %{noeud}\" | EVAL recent = CASE(@timestamp >= NOW() - 3 minutes, 1, 0), down_recent = CASE(@timestamp >= NOW() - 3 minutes AND monitor.status == \"down\", 1, 0), up_ts = CASE(monitor.status == \"up\", @timestamp, NULL), err = CASE(@timestamp >= NOW() - 3 minutes, TO_STRING(error.message), NULL) | STATS verifs = SUM(recent), echecs = SUM(down_recent), dernier_up = MAX(up_ts), erreur = MAX(err), ip = MAX(monitor.ip) BY host.name = noeud, monitor.name, observer.geo.name | EVAL dernier_succes = COALESCE(TO_STRING(dernier_up), \"aucun dans la dernière heure\"), erreur = COALESCE(erreur, \"inconnue\") | WHERE verifs >= 2 AND echecs == verifs | KEEP host.name, monitor.name, observer.geo.name, verifs, echecs, dernier_succes, erreur"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-synth-p1-ping-down",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Le nœud ne répond plus au ping depuis l'emplacement {{#context.hits}}{{_source.observer.geo.name}}{{/context.hits}} : {{#context.hits}}{{_source.echecs}}{{/context.hits}}/{{#context.hits}}{{_source.verifs}}{{/context.hits}} échecs sur 3 min ({{#context.hits}}{{_source.erreur}}{{/context.hits}}). Dernier succès : {{#context.hits}}{{_source.dernier_succes}}{{/context.hits}}.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "kb-synth-p1-ping-down",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Le nœud répond de nouveau au ping",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### Optionnelle : `logstash-pq-time-to-full` (créée désactivée)

File persistante pleine dans moins de 30 min au rythme de croissance actuel (optionnelle, désactivée dans le lab).

```
POST kbn:/api/alerting/rule/logstash-pq-time-to-full
{
  "name": "[P1] Logstash – file persistante pleine dans moins de 30 min",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "enabled": false,
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "logstash-pq-time-to-full",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "File persistante remplie à {{#context.hits}}{{_source.fill_pct}}{{/context.hits}} % ({{#context.hits}}{{_source.events}}{{/context.hits}} événements en attente) : pleine dans {{#context.hits}}{{_source.minutes_to_full}}{{/context.hits}} min au rythme actuel.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "logstash-pq-time-to-full",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "La file n'est plus en voie de saturation",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

---

## 4. Partie B – nœuds surveillés par les Beats

### 4.1 Utilisateur des Beats

```
PUT _security/role/lab_beats_writer
{
  "cluster": ["monitor", "read_ilm", "read_pipeline"],
  "indices": [{"names": ["metricbeat-*", "filebeat-*"], "privileges": ["create_doc", "view_index_metadata", "auto_configure"]}]
}

PUT _security/user/lab_beats
{
  "password": "<MOT_DE_PASSE_BEATS>",
  "roles": ["lab_beats_writer", "remote_monitoring_agent"],
  "full_name": "Metricbeat / Filebeat des nœuds Logstash"
}
```

### 4.2 Metricbeat et Filebeat sur chaque nœud

Paquets `metricbeat` et `filebeat` de la même version que la stack (dépôt Elastic). Partie commune de
`/etc/metricbeat/metricbeat.yml` et `/etc/filebeat/filebeat.yml` (remplacer `<beat>` et `<POOL>`) :

```yaml
# metricbeat.yml : metricbeat.config.modules.path: ${path.config}/modules.d/*.yml
# filebeat.yml   : filebeat.config.modules.path: ${path.config}/modules.d/*.yml
fields:
  logstash:
    pool: <POOL>
fields_under_root: true
output.elasticsearch:
  hosts: ["https://es01:9200", "https://es02:9200", "https://es03:9200"]
  username: lab_beats
  password: "${LAB_BEATS_PASSWORD}"                 # keystore du Beat
  ssl.certificate_authorities: ["/etc/<beat>/ca.crt"]
setup.template.enabled: false                        # modèles installés une fois (4.3)
setup.ilm.check_exists: false
monitoring.enabled: false
```

Mot de passe dans le keystore de chaque Beat :
`metricbeat keystore create` puis `metricbeat keystore add LAB_BEATS_PASSWORD` (idem `filebeat`).

Modules (seuls fichiers dans `modules.d/`) :

`/etc/metricbeat/modules.d/logstash-xpack.yml`
```yaml
# Logstash, format Stack Monitoring (xpack) -> data stream .monitoring-logstash-8-mb.
# 127.0.0.1 et non localhost : l'API Logstash n'écoute qu'en IPv4 locale.
- module: logstash
  xpack.enabled: true
  period: 10s
  hosts: ["http://127.0.0.1:9600"]
```

`/etc/metricbeat/modules.d/system.yml`
```yaml
# Système du nœud (process Logstash, disques / et /DATA, CPU, mémoire) -> metricbeat-*.
- module: system
  period: 10s
  metricsets: [cpu, load, memory, network, process, process_summary, socket_summary]
  processes: ['.*']
  process.include_top_n:
    by_cpu: 5
    by_memory: 5
- module: system
  period: 1m
  metricsets: [filesystem, fsstat]
  processors:
    - drop_event.when.regexp:
        system.filesystem.mount_point: '^/(sys|cgroup|proc|dev|etc|host|lib|snap|run)($|/)'
```

`/etc/filebeat/modules.d/logstash.yml`
```yaml
# Logs Logstash (installation /DATA/logstash) -> filebeat-*.
- module: logstash
  log:
    enabled: true
    var.paths: ["/DATA/logstash/logs/logstash-plain*.log"]
  slowlog:
    enabled: true
    var.paths: ["/DATA/logstash/logs/logstash-slowlog-plain*.log"]
```

### 4.3 Modèles d'index et pipelines d'ingestion (une fois, **avant** de démarrer les Beats)

Sur un nœud, avec un compte administrateur :

```
metricbeat setup --index-management -E output.elasticsearch.username=elastic -E output.elasticsearch.password=<MDP> -E setup.template.enabled=true
filebeat setup --index-management --pipelines --modules logstash --force-enable-module-filesets -E output.elasticsearch.username=elastic -E output.elasticsearch.password=<MDP> -E setup.template.enabled=true
```

Puis démarrer `metricbeat` et `filebeat` (systemd) sur chaque nœud.

### 4.4 Pipeline d'aplatissement par nœud

Le format xpack range les pipelines dans un tableau `nested` (illisible en ES\|QL) et n'a ni flow metrics ni
health report. Chaque nœud interroge donc **sa propre API** et écrit un document par pipeline, avec les
**mêmes champs que l'intégration Elastic Agent** :

| Data stream | Contenu |
|---|---|
| `metrics-logstash_flat.pipeline-lab` | Files, flow metrics, compteurs d'événements |
| `metrics-logstash_flat.health-lab` | État et statut du health report, par pipeline |
| `metrics-logstash_flat.node-lab` | Heap JVM, CPU du process |

Gestion centralisée : l'ID doit commencer par le préfixe des nœuds concernés (`<pool>-…`) ; ici `pool2-`.
Le pipeline utilise l'utilisateur `logstash_internal` (écriture sur `metrics-*`) et la variable `LS_POOL`.

```
PUT _logstash/pipeline/pool2-monitoring_flat
{
  "description": "Aplatissement par noeud des metriques Logstash",
  "last_modified": "2026-01-01T00:00:00.000Z",
  "pipeline_metadata": {
    "type": "logstash_pipeline",
    "version": 1
  },
  "username": "elastic",
  "pipeline": "# Aplatissement des métriques Logstash, par nœud (nœuds sans Elastic Agent : pool2).\n# Chaque nœud interroge SA propre API (127.0.0.1:9600) et écrit un document par pipeline :\n#   metrics-logstash_flat.pipeline-lab : files, flow metrics, compteurs (mêmes champs que\n#                                        l'intégration Logstash d'Elastic Agent)\n#   metrics-logstash_flat.health-lab   : état / statut du health report, par pipeline\n#   metrics-logstash_flat.node-lab     : heap JVM, CPU du process\n# 1 worker, file mémoire, sortie dédiée : n'interfère pas avec les pipelines métier.\ninput {\n  http_poller {\n    urls => {\n      pipelines => \"http://127.0.0.1:9600/_node/stats/pipelines\"\n      health    => \"http://127.0.0.1:9600/_health_report\"\n      node      => \"http://127.0.0.1:9600/_node/stats/jvm,process\"\n    }\n    schedule => { \"every\" => \"10s\" }\n    request_timeout => 5\n    codec => \"json\"\n    target => \"[api]\"\n  }\n}\n\nfilter {\n  # API indisponible (démarrage, arrêt) : rien à publier ; l'absence est détectée par les règles\n  if \"_http_request_failure\" in [tags] { drop {} }\n\n  ruby {\n    code => '\n      api  = event.get(\"[api]\") || {}\n      # Type de réponse déduit du contenu (indépendant de la structure des métadonnées de http_poller)\n      kind = api.key?(\"pipelines\") ? \"pipelines\" : (api.key?(\"indicators\") ? \"health\" : (api.key?(\"jvm\") ? \"node\" : nil))\n      items = []\n      case kind\n      when \"pipelines\"\n        (api[\"pipelines\"] || {}).each do |id, p|\n          q = p[\"queue\"] || {}\n          flow = {}\n          (p[\"flow\"] || {}).each do |k, v|\n            flow[k] = v.is_a?(Hash) ? v.transform_values { |x| x.is_a?(Numeric) ? x.to_f : x } : v\n          end\n          ev = p[\"events\"] || {}\n          items << { \"dataset\" => \"logstash_flat.pipeline\", \"logstash\" => { \"pipeline\" => { \"name\" => id, \"total\" => {\n            \"queues\" => { \"type\" => q[\"type\"], \"events\" => (q[\"events_count\"] || q[\"events\"] || 0).to_i,\n                          \"current_size\" => { \"bytes\" => (q[\"queue_size_in_bytes\"] || 0).to_i },\n                          \"max_size\"     => { \"bytes\" => (q[\"max_queue_size_in_bytes\"] || 0).to_i } },\n            \"flow\"   => flow,\n            \"events\" => { \"in\" => ev[\"in\"].to_i, \"out\" => ev[\"out\"].to_i, \"filtered\" => ev[\"filtered\"].to_i } } } } }\n        end\n      when \"health\"\n        ((api[\"indicators\"] || {})[\"pipelines\"] || {}).fetch(\"indicators\", {}).each do |id, h|\n          state = ((h[\"details\"] || {})[\"status\"] || {})[\"state\"] || \"UNKNOWN\"\n          items << { \"dataset\" => \"logstash_flat.health\", \"logstash\" => { \"pipeline\" => {\n            \"id\" => id, \"status\" => h[\"status\"], \"symptom\" => h[\"symptom\"], \"state\" => state } } }\n        end\n      when \"node\"\n        heap = ((api[\"jvm\"] || {})[\"mem\"] || {})[\"heap_used_percent\"]\n        cpu  = (((api[\"process\"] || {})[\"cpu\"]) || {})[\"percent\"]\n        items << { \"dataset\" => \"logstash_flat.node\", \"logstash\" => { \"node\" => { \"stats\" => {\n          \"jvm\" => { \"mem\" => { \"heap_used_percent\" => heap } }, \"process\" => { \"cpu\" => { \"percent\" => cpu } } } } } }\n      end\n      event.set(\"[@metadata][node]\", api[\"name\"])\n      event.remove(\"[api]\")\n      if items.empty? then event.cancel else event.set(\"[items]\", items) end\n    '\n  }\n\n  split { field => \"[items]\" }\n\n  ruby {\n    code => '\n      it = event.remove(\"[items]\")\n      event.set(\"[logstash]\", it[\"logstash\"])\n      event.set(\"[data_stream]\", { \"type\" => \"metrics\", \"dataset\" => it[\"dataset\"], \"namespace\" => \"lab\" })\n      event.set(\"[event][dataset]\", it[\"dataset\"])\n      node = event.get(\"[@metadata][node]\")\n      event.set(\"[host][name]\", node)\n      event.set(\"[logstash][node][name]\", node)\n    '\n  }\n  mutate {\n    add_field => { \"[logstash][pool]\" => \"${LS_POOL}\" }\n    remove_field => [\"[event][original]\", \"@version\"]\n  }\n}\n\noutput {\n  elasticsearch {\n    hosts => [\"https://es01.elk-lab.orb.local:9200\", \"https://es02.elk-lab.orb.local:9200\", \"https://es03.elk-lab.orb.local:9200\"]\n    user => \"logstash_internal\"\n    password => \"${LS_WRITER_PASSWORD}\"\n    ssl_enabled => true\n    ssl_certificate_authorities => [\"/DATA/logstash/config/certs/ca.crt\"]\n    data_stream => true\n  }\n}\n",
  "pipeline_settings": {
    "pipeline.workers": 1,
    "pipeline.batch.size": 125,
    "pipeline.batch.delay": 50,
    "queue.type": "memory"
  }
}
```

Vérification (au bout d'une minute) :

```
POST _query?format=txt
{"query": "FROM metrics-logstash_flat.* | STATS docs = COUNT(*), dernier = MAX(@timestamp) BY data_stream.dataset, host.name"}
```

### 4.5 Exclure les pools Beats des règles Elastic Agent

Les règles Agent *process absent* et *pool sans nœud sain* lisent les données Agent : sans exclusion, un pool
passé aux Beats paraîtrait muet. Leur requête contient la condition
`(logstash.pool IS NULL OR logstash.pool != "pool2")` : y lister tous les pools surveillés par les Beats.

### 4.6 Règles P1 `[BEATS]`

Mêmes seuils et messages que la partie A ; sources : `metricbeat-*`, `filebeat-*` (filtre `event.dataset`),
`metrics-logstash_flat.*`.

#### `beats-kb-ls-p1-process-absent` – [BEATS] [P1] Logstash – process absent

Process `org.logstash.Logstash` non vu depuis plus de 120 s sur un nœud, alors que les autres nœuds Logstash remontent encore leurs métriques (si l'agent du nœud est muet, l'alerte se déclenche aussi). Source : `metricbeat-*`.

```
POST kbn:/api/alerting/rule/beats-kb-ls-p1-process-absent
{
  "name": "[BEATS] [P1] Logstash – process absent",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM metricbeat-* | WHERE host.name LIKE \"logstash-*\" | INLINE STATS anchor = MAX(@timestamp) | WHERE event.dataset == \"system.process\" | EVAL ls_ts = CASE(process.command_line LIKE \"*org.logstash.Logstash*\", @timestamp, NULL) | STATS last_ls = MAX(ls_ts), anchor = MAX(anchor) BY host.name | EVAL retard_s = DATE_DIFF(\"second\", COALESCE(last_ls, NOW() - 1 hour), anchor) | WHERE retard_s > 120 | EVAL dernier_vu = COALESCE(TO_STRING(last_ls), \"aucun dans la dernière heure\") | KEEP host.name, last_ls, dernier_vu, retard_s"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-process-absent",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Process Logstash absent depuis {{#context.hits}}{{_source.retard_s}}{{/context.hits}} s (dernier vu : {{#context.hits}}{{_source.dernier_vu}}{{/context.hits}}). Vérifier : sudo startlogstash status, /DATA/logstash/logs/logstash-plain.log",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-process-absent",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Process Logstash de nouveau présent",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `beats-kb-ls-p1-restart-loop` – [BEATS] [P1] Logstash – démarrages en boucle

Plus de 2 « Starting Logstash » en 10 min sur un nœud : crash ou redémarrage en boucle. Source : `filebeat-*`.

```
POST kbn:/api/alerting/rule/beats-kb-ls-p1-restart-loop
{
  "name": "[BEATS] [P1] Logstash – démarrages en boucle",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM filebeat-* | WHERE event.dataset == \"logstash.log\" AND QSTR(\"message:\\\"Starting Logstash\\\"\") | STATS demarrages = COUNT(*) BY host.name | WHERE demarrages > 2"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-restart-loop",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "{{#context.hits}}{{_source.demarrages}}{{/context.hits}} démarrages de Logstash en 10 min : crash ou redémarrage en boucle. Voir le début de /DATA/logstash/logs/logstash-plain.log et logstash-stdout.log",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-restart-loop",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Plus de démarrage en boucle (moins de 3 démarrages sur 10 min)",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `beats-kb-ls-p1-heap` – [BEATS] [P1] Logstash – heap JVM critique (> 95 % sur 10 min)

Heap JVM moyen > 95 % sur 10 min. Source : `metrics-logstash_flat.node-*`.

```
POST kbn:/api/alerting/rule/beats-kb-ls-p1-heap
{
  "name": "[BEATS] [P1] Logstash – heap JVM critique (> 95 % sur 10 min)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM metrics-logstash_flat.node-* | STATS heap_pct = ROUND(AVG(logstash.node.stats.jvm.mem.heap_used_percent), 1) BY host.name | WHERE heap_pct > 95"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-heap",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Heap JVM à {{#context.hits}}{{_source.heap_pct}}{{/context.hits}} % en moyenne sur 10 min : GC permanent, OutOfMemoryError probable. Augmenter le heap (config/jvm.options) ou réduire la charge.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-heap",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Heap JVM revenu sous 95 %",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `beats-kb-ls-p1-oom` – [BEATS] [P1] Logstash – OutOfMemoryError

`OutOfMemoryError` dans les logs Logstash (5 min). Source : `filebeat-*`.

```
POST kbn:/api/alerting/rule/beats-kb-ls-p1-oom
{
  "name": "[BEATS] [P1] Logstash – OutOfMemoryError",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM filebeat-* | WHERE event.dataset == \"logstash.log\" AND QSTR(\"message:\\\"java.lang.OutOfMemoryError\\\" OR message:\\\"OutOfMemoryError\\\"\") | STATS occurrences = COUNT(*) BY host.name"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-oom",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "OutOfMemoryError dans les logs Logstash ({{#context.hits}}{{_source.occurrences}}{{/context.hits}} occurrence(s) en 5 min) : le process est probablement instable ou arrêté. Vérifier sudo startlogstash status.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-oom",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Plus d'OutOfMemoryError dans les logs",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `beats-kb-ls-p1-pipeline-not-running` – [BEATS] [P1] Logstash – pipeline arrêté ou en erreur (health report)

Health report : pipeline `FINISHED`, `TERMINATED` ou `LOADING`, ou statut `red` (fenêtre 3 min, 3 confirmations). `UNKNOWN` exclu (pipelines supprimés). Source : `metrics-logstash_flat.health-*`.

```
POST kbn:/api/alerting/rule/beats-kb-ls-p1-pipeline-not-running
{
  "name": "[BEATS] [P1] Logstash – pipeline arrêté ou en erreur (health report)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM metrics-logstash_flat.health-* | WHERE logstash.pipeline.state IN (\"FINISHED\", \"TERMINATED\", \"LOADING\") OR logstash.pipeline.status == \"red\" | STATS rapports = COUNT(*), etat = MAX(logstash.pipeline.state), statut = MAX(logstash.pipeline.status) BY host.name = logstash.node.name, logstash.pipeline.name = logstash.pipeline.id"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-pipeline-not-running",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Pipeline dans l'état {{#context.hits}}{{_source.etat}}{{/context.hits}} (statut {{#context.hits}}{{_source.statut}}{{/context.hits}}) d'après le health report Logstash, depuis plus de 2 min.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-pipeline-not-running",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Pipeline de nouveau RUNNING",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `beats-kb-ls-p1-shutdown-stalled` – [BEATS] [P1] Logstash – arrêt de pipeline figé

« shutdown process appears to be stalled » : arrêt de pipeline figé. Source : `filebeat-*`.

```
POST kbn:/api/alerting/rule/beats-kb-ls-p1-shutdown-stalled
{
  "name": "[BEATS] [P1] Logstash – arrêt de pipeline figé",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM filebeat-* | WHERE event.dataset == \"logstash.log\" AND QSTR(\"message:\\\"shutdown process appears to be stalled\\\"\") | STATS occurrences = COUNT(*) BY host.name"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-shutdown-stalled",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Arrêt de pipeline figé (shutdown stalled, {{#context.hits}}{{_source.occurrences}}{{/context.hits}} fois en 5 min) : workers bloqués, typiquement sur une sortie qui réessaie. Le pipeline est injoignable pendant ce temps.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-shutdown-stalled",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Plus d'arrêt de pipeline figé",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `beats-kb-ls-p1-pool-down` – [BEATS] [P1] Logstash – pool sans aucun nœud sain

Aucun nœud d'un pool ne remonte de métriques de pipeline depuis plus de 180 s (par rapport aux autres nœuds). Source : `metrics-logstash_flat.pipeline-*,metricbeat-*`.

```
POST kbn:/api/alerting/rule/beats-kb-ls-p1-pool-down
{
  "name": "[BEATS] [P1] Logstash – pool sans aucun nœud sain",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM metrics-logstash_flat.pipeline-*,metricbeat-* | WHERE host.name LIKE \"logstash-*\" | INLINE STATS anchor = MAX(@timestamp) | WHERE data_stream.dataset == \"logstash_flat.pipeline\" | STATS last_seen = MAX(@timestamp), anchor = MAX(anchor) BY logstash.pool | EVAL retard_s = DATE_DIFF(\"second\", last_seen, anchor) | WHERE retard_s > 180 | KEEP logstash.pool, last_seen, retard_s"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-pool-down",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.logstash.pool}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Aucun nœud du pool {{#context.hits}}{{_source.logstash.pool}}{{/context.hits}} ne remonte de métriques de pipeline depuis {{#context.hits}}{{_source.retard_s}}{{/context.hits}} s (dernière : {{#context.hits}}{{_source.last_seen}}{{/context.hits}}) : tous les flux du pool sont interrompus.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-pool-down",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Le pool remonte de nouveau des métriques",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `beats-kb-ls-p1-p2p-broken` – [BEATS] [P1] Logstash – chaîne _in → _out coupée

Plus de 30 « address was unavailable » en 2 min : le pipeline `_in` n'atteint plus son `_out`. Source : `filebeat-*`.

```
POST kbn:/api/alerting/rule/beats-kb-ls-p1-p2p-broken
{
  "name": "[BEATS] [P1] Logstash – chaîne _in → _out coupée",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM filebeat-* | WHERE event.dataset == \"logstash.log\" AND QSTR(\"message:\\\"address was unavailable\\\"\") | STATS tentatives = COUNT(*) BY host.name, logstash.pipeline.name = logstash.log.pipeline_id | WHERE tentatives > 30"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-p2p-broken",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Le pipeline _in n'arrive plus à envoyer vers son pipeline _out : {{#context.hits}}{{_source.tentatives}}{{/context.hits}} tentatives en 2 min (destination arrêtée, figée ou en rechargement). L'input est bloqué.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-p2p-broken",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Chaîne _in → _out rétablie",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `beats-kb-ls-p1-es-unreachable` – [BEATS] [P1] Logstash – Elasticsearch injoignable

« Elasticsearch Unreachable » ou « Marking url as dead » : Logstash n'atteint plus Elasticsearch. Source : `filebeat-*`.

```
POST kbn:/api/alerting/rule/beats-kb-ls-p1-es-unreachable
{
  "name": "[BEATS] [P1] Logstash – Elasticsearch injoignable",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM filebeat-* | WHERE event.dataset == \"logstash.log\" AND QSTR(\"message:\\\"Elasticsearch Unreachable\\\" OR message:\\\"Marking url as dead\\\"\") | STATS erreurs = COUNT(*) BY host.name"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-es-unreachable",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Elasticsearch injoignable depuis Logstash : {{#context.hits}}{{_source.erreurs}}{{/context.hits}} erreur(s) en 2 min (Elasticsearch Unreachable / Marking url as dead). Les files persistantes vont se remplir, puis les inputs se bloquer.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-es-unreachable",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{alert.id}}",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Logstash joint de nouveau Elasticsearch",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `beats-kb-ls-p1-index-rejected` – [BEATS] [P1] Logstash – événements rejetés par Elasticsearch (perte de données)

« Could not index event » : documents rejetés définitivement (perte sans DLQ). Source : `filebeat-*`.

```
POST kbn:/api/alerting/rule/beats-kb-ls-p1-index-rejected
{
  "name": "[BEATS] [P1] Logstash – événements rejetés par Elasticsearch (perte de données)",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM filebeat-* | WHERE event.dataset == \"logstash.log\" AND QSTR(\"message:\\\"Could not index event\\\"\") | STATS rejets = COUNT(*) BY host.name, logstash.pipeline.name = logstash.log.pipeline_id"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-index-rejected",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "{{#context.hits}}{{_source.rejets}}{{/context.hits}} événement(s) rejeté(s) définitivement par Elasticsearch en 5 min (conflit de mapping, erreur 400) : perte de données sans file d'événements rejetés (DLQ).",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-index-rejected",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Plus d'événement rejeté par Elasticsearch",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### `beats-kb-ls-p1-port-in-use` – [BEATS] [P1] Logstash – port d’écoute déjà utilisé

« Address already in use » : un input n'a pas pu ouvrir son port. Source : `filebeat-*`.

```
POST kbn:/api/alerting/rule/beats-kb-ls-p1-port-in-use
{
  "name": "[BEATS] [P1] Logstash – port d’écoute déjà utilisé",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "tags": [
    "logstash",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM filebeat-* | WHERE event.dataset == \"logstash.log\" AND QSTR(\"message:\\\"Address already in use\\\"\") | STATS occurrences = COUNT(*) BY host.name, logstash.pipeline.name = logstash.log.pipeline_id"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-port-in-use",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "Un input n'a pas pu ouvrir son port (Address already in use, {{#context.hits}}{{_source.occurrences}}{{/context.hits}} fois en 5 min) : le pipeline ne démarre pas.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-kb-ls-p1-port-in-use",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "Plus d'erreur de port déjà utilisé",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

#### Optionnelle : `beats-logstash-pq-time-to-full` (créée désactivée)

```
POST kbn:/api/alerting/rule/beats-logstash-pq-time-to-full
{
  "name": "[BEATS] [P1] Logstash – file persistante pleine dans moins de 30 min",
  "rule_type_id": ".es-query",
  "consumer": "stackAlerts",
  "enabled": false,
  "tags": [
    "logstash",
    "queue",
    "elk-lab",
    "P1",
    "beats"
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
      "esql": "FROM metrics-logstash_flat.pipeline-* | WHERE logstash.pipeline.total.queues.type == \"persisted\" | STATS used = MAX(logstash.pipeline.total.queues.current_size.bytes), max = MAX(logstash.pipeline.total.queues.max_size.bytes), events = MAX(logstash.pipeline.total.queues.events), growth_events = AVG(logstash.pipeline.total.flow.queue_persisted_growth_events.last_1_minute), growth = AVG(logstash.pipeline.total.flow.queue_persisted_growth_bytes.last_1_minute) BY host.name, logstash.pipeline.name | WHERE events > 0 AND growth_events > 0 AND growth > 0 | EVAL fill_pct = ROUND(100.0 * used / max, 1), minutes_to_full = ROUND(TO_DOUBLE(max - used) / growth / 60.0, 1) | WHERE minutes_to_full < 30 | KEEP host.name, logstash.pipeline.name, events, fill_pct, minutes_to_full | SORT minutes_to_full"
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
      "id": "alerts-elklab-index",
      "group": "query matched",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-logstash-pq-time-to-full",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "{{#context.hits}}{{_source.host.name}}{{/context.hits}}",
            "pipeline": "{{#context.hits}}{{_source.logstash.pipeline.name}}{{/context.hits}}",
            "severity": "critical",
            "priority": "P1",
            "status": "firing",
            "problem": "File persistante remplie à {{#context.hits}}{{_source.fill_pct}}{{/context.hits}} % ({{#context.hits}}{{_source.events}}{{/context.hits}} événements en attente) : pleine dans {{#context.hits}}{{_source.minutes_to_full}}{{/context.hits}} min au rythme actuel.",
            "time": "{{date}}"
          }
        ]
      }
    },
    {
      "id": "alerts-elklab-index",
      "group": "recovered",
      "frequency": {
        "summary": false,
        "notify_when": "onActionGroupChange",
        "throttle": null
      },
      "params": {
        "documents": [
          {
            "rule_id": "beats-logstash-pq-time-to-full",
            "rule": "{{rule.name}}",
            "alert_id": "{{alert.id}}",
            "host": "",
            "pipeline": "",
            "severity": "ok",
            "priority": "P1",
            "status": "recovered",
            "problem": "La file n'est plus en voie de saturation",
            "time": "{{date}}"
          }
        ]
      }
    }
  ]
}
```

---

## 5. Grafana : panels *Alertes en cours* et *Historique des alertes*

### 5.1 Datasource

*Connections → Data sources → Add → Elasticsearch* :

| Champ | Valeur |
|---|---|
| Name / UID | `Journal des alertes` / **`alerts-elklab`** (l'UID est référencé par les panels) |
| URL | `https://es01:9200` |
| Auth | Basic auth `grafana_reader` ; *With CA cert* : CA du cluster |
| Index name | `alerts-elklab` |
| Time field name | **`time`** |

*Save & test* doit répondre « Elasticsearch data source is healthy ».

### 5.2 Tableau de bord

*Dashboards → New → Import*, coller le JSON ci-dessous. Chaque panel peut aussi être collé seul
(*Add → Panel → ⋮ → Inspect → Panel JSON*).

- **Alertes en cours** : dernier événement de chaque alerte (`alert_id`), gardé s'il est `firing`, sur 30 jours
  quelle que soit la période affichée.
- **Historique des alertes** : tous les événements de la période ; nœud et pipeline des levées repris du
  déclenchement.
- Colonne *Rule* : le préfixe de priorité est retiré (colonne *Priority*), `[BEATS]` est conservé.

```json
{
  "title": "Alertes Logstash",
  "uid": "alertes-logstash",
  "schemaVersion": 39,
  "time": {
    "from": "now-24h",
    "to": "now"
  },
  "refresh": "30s",
  "tags": [
    "logstash",
    "alerting"
  ],
  "panels": [
    {
      "id": 1,
      "type": "table",
      "title": "Alertes en cours",
      "description": "Alertes Kibana en cours, d’après le journal alerts-elklab : dernier événement de chaque alerte (alert_id), gardé s’il est « firing ». Message parlant (Problem) écrit par chaque règle. Les 30 derniers jours sont toujours pris en compte, quelle que soit la période du tableau de bord.",
      "gridPos": {
        "x": 0,
        "y": 0,
        "w": 24,
        "h": 8
      },
      "datasource": {
        "type": "elasticsearch",
        "uid": "alerts-elklab"
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "elasticsearch",
            "uid": "alerts-elklab"
          },
          "queryType": "esql",
          "editorType": "code",
          "query": "FROM alerts-elklab\n| WHERE time >= NOW() - 30 days\n| INLINE STATS dernier = MAX(time) BY alert_id, rule_id\n| WHERE time == dernier AND status == \"firing\"\n| EVAL Priority = priority, Severity = severity, Host = host, Pipeline = pipeline,\n       Problem = REPLACE(problem, \"([0-9]) B \", \"$1 % \"),\n       Rule = REPLACE(rule, \"^(\\\\[BEATS\\\\] )?\\\\[P[0-9/P]*\\\\] \", \"$1\"), Time = time\n| KEEP Priority, Severity, Host, Pipeline, Problem, Time, Rule\n| SORT Priority ASC, Time DESC\n| LIMIT 500",
          "timeField": "time",
          "metrics": [
            {
              "id": "1",
              "type": "raw_data",
              "settings": {
                "size": "1000"
              }
            }
          ]
        }
      ],
      "transformations": [],
      "options": {
        "showHeader": true,
        "cellHeight": "sm",
        "footer": {
          "show": true,
          "reducer": [
            "count"
          ],
          "fields": [
            "Problem"
          ],
          "countRows": true
        }
      },
      "fieldConfig": {
        "defaults": {
          "custom": {
            "align": "auto",
            "cellOptions": {
              "type": "auto"
            },
            "inspect": true,
            "filterable": true
          }
        },
        "overrides": [
          {
            "matcher": {
              "id": "byName",
              "options": "Priority"
            },
            "properties": [
              {
                "id": "custom.cellOptions",
                "value": {
                  "type": "color-background",
                  "mode": "basic"
                }
              },
              {
                "id": "custom.width",
                "value": 80
              },
              {
                "id": "mappings",
                "value": [
                  {
                    "type": "value",
                    "options": {
                      "P1": {
                        "color": "red",
                        "index": 0
                      },
                      "P2": {
                        "color": "orange",
                        "index": 1
                      },
                      "P3": {
                        "color": "blue",
                        "index": 2
                      },
                      "P2/P1": {
                        "color": "text",
                        "index": 3
                      }
                    }
                  }
                ]
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Severity"
            },
            "properties": [
              {
                "id": "custom.cellOptions",
                "value": {
                  "type": "color-text",
                  "mode": "basic"
                }
              },
              {
                "id": "custom.width",
                "value": 90
              },
              {
                "id": "mappings",
                "value": [
                  {
                    "type": "value",
                    "options": {
                      "critical": {
                        "color": "red",
                        "index": 0
                      },
                      "warning": {
                        "color": "orange",
                        "index": 1
                      },
                      "ok": {
                        "color": "green",
                        "index": 2
                      },
                      "info": {
                        "color": "blue",
                        "index": 3
                      }
                    }
                  }
                ]
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Time"
            },
            "properties": [
              {
                "id": "unit",
                "value": "dateTimeAsLocal"
              },
              {
                "id": "custom.width",
                "value": 170
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Host"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 150
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Pipeline"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 190
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Rule"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 300
              }
            ]
          }
        ]
      }
    },
    {
      "id": 2,
      "type": "table",
      "title": "Historique des alertes",
      "gridPos": {
        "x": 0,
        "y": 8,
        "w": 24,
        "h": 12
      },
      "datasource": {
        "type": "elasticsearch",
        "uid": "alerts-elklab"
      },
      "targets": [
        {
          "refId": "A",
          "datasource": {
            "type": "elasticsearch",
            "uid": "alerts-elklab"
          },
          "queryType": "esql",
          "editorType": "code",
          "query": "FROM alerts-elklab\n| INLINE STATS h = MAX(host), p = MAX(pipeline) BY alert_id, rule_id\n| EVAL Host = CASE(host IS NULL OR host == \"\", h, host),\n       Pipeline = CASE(pipeline IS NULL OR pipeline == \"\", p, pipeline),\n       Status = status, Priority = priority, Severity = severity,\n       Problem = REPLACE(problem, \"([0-9]) B \", \"$1 % \"),\n       Rule = REPLACE(rule, \"^(\\\\[BEATS\\\\] )?\\\\[P[0-9/P]*\\\\] \", \"$1\"), Time = time\n| KEEP Time, Status, Priority, Severity, Host, Pipeline, Problem, Rule\n| SORT Time DESC\n| LIMIT 1000",
          "timeField": "time",
          "metrics": [
            {
              "id": "1",
              "type": "raw_data",
              "settings": {
                "size": "1000"
              }
            }
          ]
        }
      ],
      "transformations": [],
      "options": {
        "showHeader": true,
        "cellHeight": "sm",
        "footer": {
          "show": true,
          "reducer": [
            "count"
          ],
          "fields": [
            "Problem"
          ],
          "countRows": true
        }
      },
      "fieldConfig": {
        "defaults": {
          "custom": {
            "align": "auto",
            "cellOptions": {
              "type": "auto"
            },
            "inspect": true,
            "filterable": true
          }
        },
        "overrides": [
          {
            "matcher": {
              "id": "byName",
              "options": "Status"
            },
            "properties": [
              {
                "id": "custom.cellOptions",
                "value": {
                  "type": "color-text",
                  "mode": "basic"
                }
              },
              {
                "id": "custom.width",
                "value": 95
              },
              {
                "id": "mappings",
                "value": [
                  {
                    "type": "value",
                    "options": {
                      "firing": {
                        "color": "orange",
                        "index": 0
                      },
                      "recovered": {
                        "color": "green",
                        "index": 1
                      }
                    }
                  }
                ]
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Priority"
            },
            "properties": [
              {
                "id": "custom.cellOptions",
                "value": {
                  "type": "color-background",
                  "mode": "basic"
                }
              },
              {
                "id": "custom.width",
                "value": 80
              },
              {
                "id": "mappings",
                "value": [
                  {
                    "type": "value",
                    "options": {
                      "P1": {
                        "color": "red",
                        "index": 0
                      },
                      "P2": {
                        "color": "orange",
                        "index": 1
                      },
                      "P3": {
                        "color": "blue",
                        "index": 2
                      },
                      "P2/P1": {
                        "color": "text",
                        "index": 3
                      }
                    }
                  }
                ]
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Severity"
            },
            "properties": [
              {
                "id": "custom.cellOptions",
                "value": {
                  "type": "color-text",
                  "mode": "basic"
                }
              },
              {
                "id": "custom.width",
                "value": 90
              },
              {
                "id": "mappings",
                "value": [
                  {
                    "type": "value",
                    "options": {
                      "critical": {
                        "color": "red",
                        "index": 0
                      },
                      "warning": {
                        "color": "orange",
                        "index": 1
                      },
                      "ok": {
                        "color": "green",
                        "index": 2
                      },
                      "info": {
                        "color": "blue",
                        "index": 3
                      }
                    }
                  }
                ]
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Time"
            },
            "properties": [
              {
                "id": "unit",
                "value": "dateTimeAsLocal"
              },
              {
                "id": "custom.width",
                "value": 170
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Host"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 150
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Pipeline"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 190
              }
            ]
          },
          {
            "matcher": {
              "id": "byName",
              "options": "Rule"
            },
            "properties": [
              {
                "id": "custom.width",
                "value": 300
              }
            ]
          }
        ]
      },
      "description": "Journal alerts-elklab sur la période du tableau de bord : un événement par déclenchement (firing, avec sa sévérité) et par levée (recovered). Nœud et pipeline des levées repris du déclenchement correspondant."
    }
  ]
}
```

> Piège Grafana 13 : une requête ES\|QL **sans champ `metrics`** dans la cible renvoie un résultat vide, sans
> erreur. Le JSON ci-dessus le contient ; le conserver si la requête est modifiée à la main.

---

## 6. Recette

### 6.1 Contrôles après déploiement

```
GET kbn:/api/alerting/rules/_find?per_page=100&filter=alert.attributes.tags:P1&fields=name,enabled,execution_status

POST _query?format=txt
{"query": "FROM alerts-elklab | STATS evenements = COUNT(*), dernier = MAX(time) BY rule, status | SORT dernier DESC"}
```

Toutes les règles : `enabled: true` et `execution_status.status` = `ok` (ou `active` si une anomalie existe).

### 6.2 Scénarios de test

| Règle | Test | Attendu |
|---|---|---|
| Démarrages en boucle, OOM, arrêt figé, ES injoignable, rejets, port déjà utilisé, chaîne `_in` → `_out` | Écrire 3 lignes de test dans **un fichier séparé** `/DATA/logstash/logs/logstash-plain-test.log` (Logstash réécrit par-dessus les lignes ajoutées à `logstash-plain.log`), au format `[2026-01-01T00:00:00,000][INFO ][logstash.runner ] TEST-ALERTE Starting Logstash` | Document `firing` dans `alerts-elklab` en 1 à 2 min ; supprimer le fichier ensuite |
| Process absent, pool sans nœud sain | Arrêt de Logstash sur un nœud (hors heures de production, trafic retiré du nœud) | `firing` en 3 à 4 min, `recovered` après redémarrage |
| Pipeline arrêté (health report) | Blocage de l'écriture de l'index de sortie d'un pipeline : `PUT <index>/_settings {"index.blocks.write": true}` puis `null` | Statut `red` détecté |
| Input injoignable, ping | Arrêt du service ou blocage ICMP (pare-feu) sur un nœud | `firing` en 3 à 4 min |
| Port fermé sur tout le pool | Ajouter à l'inventaire un port qu'aucun nœud n'ouvre, puis le retirer | `firing` puis `recovered` |

### 6.3 Nettoyage des tests

```
POST alerts-elklab/_delete_by_query?refresh=true
{"query": {"term": {"rule_id": "TEST"}}}
```

---

## 7. Retour arrière

Supprimer dans l'ordre inverse (une requête par règle) :

```
DELETE kbn:/api/alerting/rule/<ID_DE_LA_REGLE>
DELETE kbn:/api/actions/connector/alerts-elklab-index
DELETE _index_template/alerts-elklab
DELETE alerts-elklab-*
DELETE _ilm/policy/alerts-elklab
DELETE _logstash/pipeline/pool2-monitoring_flat
```

---

## Annexe : pièges rencontrés dans le lab

| Piège | Parade |
|---|---|
| `{{context.value}}` d'une règle custom threshold affiche l'unité du champ (« 38.4 B » pour un %) | Remplacé par « % » dans les requêtes Grafana |
| `{{context.threshold}}` est vide | Seuil écrit en dur dans le message |
| Valeurs ES\|QL (`context.hits`) vides à la levée | Nœud repris de `{{alert.id}}` ou du déclenchement (Grafana) |
| `LOOKUP JOIN` écrase les colonnes de même nom (null sans correspondance) | Copier la colonne avant la jointure |
| Identifiant d'alerte = colonnes du `STATS … BY` encore présentes en sortie | Garder dans `KEEP` les colonnes qui identifient l'alerte |
| Osquery n'envoie rien quand une requête renvoie 0 ligne | Ligne heartbeat dans le pack |
| Documents Logstash sans nom de pipeline quand l'API ne répond plus | Filtre `logstash.pipeline.name IS NOT NULL` |
| Fichier de provisioning Grafana invalide : Grafana en boucle de redémarrage | Valider le YAML avant de le déposer ; préférer l'API de rechargement (`POST /api/admin/provisioning/datasources/reload`) |
| Règle P1 *health report* : relevés `LOADING` du démarrage dans la fenêtre | Faux positif possible à chaque démarrage de Logstash (correctif : ne garder que le dernier relevé par pipeline) |

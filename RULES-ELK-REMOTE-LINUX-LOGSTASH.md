# Règles Kibana elk-remote : disque Linux et process Logstash

Quatre règles ES|QL actives sur le cluster **elk-remote** (Kibana `http://localhost:5602`), qui écrivent
leurs alertes dans le journal `alerts-elklab`. Elles remplacent les anciennes règles de même rôle,
désactivées sur elk-remote.

| ID | Nom | Sévérité | Ce qui déclenche |
|---|---|---|---|
| `linux-disk-usage` | [P2] Linux Disk Usage - warning | warning / P2 | `/` ou `/DATA` rempli à 70 % ou plus |
| `ls-process-cpu` | [P2] Logstash – process CPU usage | warning / P2 | le JVM Logstash consomme 80 % ou plus du CPU **de la machine** (moyenne sur 5 min) |
| `ls-process-memory` | [P2] Logstash – process memory usage | warning / P2 | la mémoire résidente (RSS) du JVM Logstash atteint 80 % ou plus de la **RAM de la machine** |
| `ls-process-absent` | [P1] Logstash – process absent | critical / P1 | plus de process Logstash depuis plus d'1 minute alors que l'agent remonte toujours |

Anciennes règles désactivées, remplacées par celles-ci :

| Ancienne règle | Remplacée par |
|---|---|
| `linux-server-disk` | `linux-disk-usage` (warning seul, plus de P1) |
| `linux-process-cpu` | `ls-process-cpu` |
| `kb-ls-p1-process-absent` | `ls-process-absent` |

---

## 1. Fonctionnement commun

### Paramètres de la règle (type Elasticsearch query, ES|QL)

| Paramètre | Valeur | Effet |
|---|---|---|
| `searchType` | `esqlQuery` | règle ES|QL |
| `groupBy` | `row` | **une alerte par ligne** renvoyée par la requête |
| `timeField` | `@timestamp` | Kibana ajoute lui-même le filtre `@timestamp` sur la fenêtre de temps |
| `timeWindowSize` | 5 ou 15 min | fenêtre de données lue à chaque exécution |
| `schedule.interval` | 1 min | fréquence d'exécution |
| `alert_delay.active` | 2 ou 3 | nombre d'exécutions **consécutives** avec une ligne avant que l'alerte soit levée (anti faux positif) |
| `threshold` | `> 0` | il suffit qu'une ligne soit renvoyée : le seuil métier est **dans la requête** (`WHERE … >= …`) |

### Identifiant d'alerte (`alert_id`)

Avec `groupBy: row`, l'`alert_id` est la concaténation, séparée par des virgules, des valeurs des colonnes du
**dernier `STATS … BY`**. Comme `STATS BY` produit une seule ligne par combinaison de clés, **deux lignes ne
peuvent jamais avoir le même `alert_id`** : pas de doublon.

| Règle | Clés du `BY` | Exemple d'`alert_id` |
|---|---|---|
| `linux-disk-usage` | `host.name`, `system.filesystem.mount_point` | `logstash-1,/DATA` |
| `ls-process-cpu` | `host.name` | `logstash-1` |
| `ls-process-memory` | `host.name` | `logstash-1` |
| `ls-process-absent` | `host.name` | `logstash-1` |

Le PID n'est **jamais** dans les clés : un redémarrage de Logstash ne change pas l'identité de l'alerte.

### Action : écriture dans `alerts-elklab`

Chaque règle a deux actions sur le connecteur `lab-index-alerts-elklab` (type Index), déclenchées **au
changement de groupe** (`onActionGroupChange`) : un document à la levée, un document au retour à la normale.

| Champ du document | Levée (`query matched`) | Retour (`recovered`) |
|---|---|---|
| `rule_id` | ID de la règle | idem |
| `rule` | `{{rule.name}}` | idem |
| `alert_id` / `alert_uuid` | `{{alert.id}}` / `{{alert.uuid}}` | idem (même `alert_uuid` : sert au calcul de durée dans Grafana) |
| `host` | `host.name` de la ligne | vide (les valeurs de ligne ne sont pas disponibles au retour) |
| `severity` / `priority` | fixes, voir le tableau en tête | `ok` / priorité de la règle |
| `status` | `firing` | `recovered` |
| `problem` | message avec les valeurs de la ligne | message avec `{{alert.id}}` |
| `time` | `{{date}}` | idem |

Les valeurs de la ligne sont lues avec `{{#context.hits}}{{_source.<colonne>}}{{/context.hits}}`.
Severity et priority sont **fixées dans l'action**, pas calculées dans la requête (une seule sévérité par règle).

---

## 2. `linux-disk-usage` : [P2] Linux Disk Usage - warning

```esql
FROM metrics-system.filesystem-*
| WHERE host.os.type == "linux"
      AND system.filesystem.mount_point IN ("/", "/DATA")
      AND system.filesystem.total > 0
| STATS max_ratio = MAX(system.filesystem.used.pct)
    BY host.name, system.filesystem.mount_point
| EVAL percent_fsys = ROUND(max_ratio * 100, 2)
| WHERE percent_fsys >= 70
| KEEP host.name, system.filesystem.mount_point, percent_fsys
```

Fenêtre 5 min · toutes les 1 min · `alert_delay` 2

| Ligne | Rôle |
|---|---|
| `host.os.type == "linux"` | exclut Windows / macOS (macOS a aussi un `/`) |
| `mount_point IN ("/", "/DATA")` | les deux partitions du standard |
| `total > 0` | sécurité : écarte un montage sans taille (réseau injoignable, pseudo-filesystem) ; sans effet sur `/` et `/DATA` |
| `MAX(used.pct)` | pire valeur sur 5 min, par hôte et par montage |
| `WHERE percent_fsys >= 70` | **le seuil** : sans lui, chaque disque serait une alerte |

**Message :** `Disk /DATA on logstash-1 at 72.4 % (WARNING threshold 70 %)`

**Limites :**
- Un disque qui oscille autour de 70 % produit une suite levée/retour ; `alert_delay` 2 limite l'effet
  (à monter à 3-5 si besoin).
- Un serveur à 72 % par construction garde une alerte active en permanence : ajuster le seuil ou l'exclure.
- Seuil en % uniquement : 70 % d'un disque de 2 To laisse 600 Go libres. Sur un parc hétérogène, combiner
  avec l'espace libre (`system.filesystem.available`).
- Les autres partitions (`/var`, `/var/log`, `/opt`) ne sont pas surveillées.

---

## 3. `ls-process-cpu` : [P2] Logstash – process CPU usage

```esql
FROM metrics-system.process-*
| WHERE process.executable LIKE "/DATA/logstash*"
| STATS cpu_pct = ROUND(AVG(system.process.cpu.total.norm.pct) * 100, 2)
    BY host.name
| WHERE cpu_pct >= 80
```

Fenêtre 5 min · toutes les 1 min · `alert_delay` 3

### Quel que soit le nombre de cœurs

| Champ | Signification |
|---|---|
| `system.process.cpu.total.pct` | non normalisé, comme `%CPU` de `top` : 1.0 = 1 cœur plein, jusqu'à N cœurs (ex. 10.0 sur 10 cœurs) |
| `system.process.cpu.total.norm.pct` | **normalisé** = `total.pct / nombre de cœurs` : 0 à 1 = part de la **machine** |

Vérifié sur le lab : `total.pct / norm.pct` ≈ 10 = `system.cpu.cores` des VM logstash-1/2.

Logstash occupant 2 cœurs à 100 % :

| Hôte | `total.pct` | `cpu_pct` (la règle) |
|---|---|---|
| 2 cœurs | 2.0 | 100 % |
| 4 cœurs | 2.0 | 50 % |
| 10 cœurs | 2.0 | 20 % |
| 32 cœurs | 2.0 | 6,25 % |

Le seuil de 80 % a donc le même sens partout : **Logstash consomme 80 % de la capacité CPU du serveur**.

### Pourquoi c'est sans faux positif

| Risque | Traitement |
|---|---|
| Pic ponctuel (GC, démarrage, rechargement de pipeline) | `AVG` sur ≈ 30 échantillons (collecte toutes les 10 s) + `alert_delay` 3 |
| Redémarrage de Logstash (nouveau PID) | **moyenne** et non somme : l'ancien et le nouveau PID ne s'additionnent pas (un seul JVM Logstash par serveur) |
| Autres process sous `/DATA/logstash` | `startlogstash.sh` tourne sous `/usr/bin/bash` (non pris) ; un outil Java court (`logstash-keystore`) fait **baisser** la moyenne |
| Doublon d'alerte | `BY host.name` : une ligne par serveur |

Le filtre porte sur `process.executable` (`/DATA/logstash/jdk/bin/java`), plus fiable que
`process.working_directory`, qui dépend de la façon dont le process a été lancé.

**Message :** `Logstash CPU on logstash-1 at 85.3 % of the host (5 min average, WARNING threshold 80 %)`

**Limites :**
- Pourcentage de la **machine** : sur 32 cœurs, Logstash peut saturer ses workers (4 cœurs) à 12,5 % sans
  alerte. La saturation des workers se lit dans les métriques de pipeline (`worker_utilization`).
- Limite CPU par cgroup (conteneur, `CPUQuota`) : la division se fait par les cœurs de l'hôte, pas par la
  limite. Ne concerne pas les VM du lab.
- Deux Logstash sur un même serveur seraient moyennés et non additionnés (hors architecture).
- La saturation globale du serveur (iowait, steal, autres process) relève d'une règle CPU machine
  (`metrics-system.cpu-*`, `system.cpu.total.norm.pct`).

---

## 4. `ls-process-memory` : [P2] Logstash – process memory usage

```esql
FROM metrics-system.process-*
| WHERE process.executable LIKE "/DATA/logstash*"
| STATS mem_pct = ROUND(AVG(system.process.memory.rss.pct) * 100, 2)
    BY host.name
| WHERE mem_pct >= 80
```

Fenêtre 5 min · toutes les 1 min · `alert_delay` 3

`system.process.memory.rss.pct` = RSS du process / RAM totale de l'hôte, de 0 à 1, quelle que soit la RAM du
serveur. Vérifié sur le lab : 36,7 Mo de RSS / 21 Go de RAM = 0,17 %.

| Hôte | RSS Logstash | `mem_pct` |
|---|---|---|
| 8 Go | 6 Go | 75 % |
| 16 Go | 6 Go | 37,5 % |

Mêmes garanties que la règle CPU : moyenne, pas de somme ; un seul JVM ; `BY host.name`. Après un redémarrage,
le nouveau PID a un RSS faible, ce qui fait **baisser** la moyenne.

### Ce que la règle mesure, et ce qu'elle ne mesure pas

```
RSS ≈ heap (-Xmx) + metaspace + mémoire native (threads, buffers réseau) + page mappée de la file persistante
```

Avec `-Xms` = `-Xmx`, le heap est réservé d'avance et le RSS bouge peu avec la charge.

| Situation | Détectée ? |
|---|---|
| Heap trop grand pour la VM (`-Xmx` 12g sur 16 Go) : risque d'**OOM killer** du noyau | oui |
| Fuite de mémoire **native** (hors heap) | oui |
| **Heap saturé** (GC en boucle, `OutOfMemoryError`) | **non** : le RSS ne bouge pas, voir `kb-ls-p1-heap` (métrique JVM `heap_used_percent`) |

**Seuil :** heap recommandé ≤ 50 % de la RAM, donc RSS normal ≈ 55 à 65 %. À 80 %, il reste peu de place pour
l'OS, le page cache et l'agent. Un seuil de 70 % déclencherait en permanence sur une VM au heap à 50 %.

**Message :** `Logstash memory (RSS) on logstash-1 at 82.1 % of the host RAM (5 min average, WARNING threshold 80 %). Risk of kernel OOM killer.`

---

## 5. `ls-process-absent` : [P1] Logstash – process absent

```esql
FROM metrics-system.process-*
| WHERE host.name LIKE "logstash-*"
| EVAL ls_ts = CASE(process.executable LIKE "/DATA/logstash*", @timestamp, NULL)
| STATS last_ls = MAX(ls_ts),
        last_seen = MAX(@timestamp)
    BY host.name
| WHERE last_ls IS NULL OR last_ls < last_seen - 1 minute
```

Fenêtre **15 min** · toutes les 1 min · `alert_delay` 2

### Principe

ES|QL ne renvoie que des documents qui existent : un process arrêté ne produit plus rien. Il faut donc une
**référence** qui continue d'arriver : les **autres process du même hôte**, que l'agent envoie toutes les
10 s.

| Colonne | Signification |
|---|---|
| `last_seen` | dernier document process de l'hôte, quel qu'il soit : **l'agent est vivant** |
| `last_ls` | dernier document du JVM Logstash (null s'il n'a pas été vu sur 15 min) |

L'alerte se déclenche si Logstash est absent, ou en retard de plus d'1 minute sur l'hôte.

### Pourquoi `last_seen - 1 minute` et pas `NOW() - 1 minute`

La référence est **l'hôte lui-même**. En cas de retard d'ingestion (Elasticsearch chargé, agent qui renvoie
ses données après une coupure réseau), `last_seen` et `last_ls` reculent ensemble : pas d'alerte. Avec
`NOW()`, tous les Logstash passeraient en alerte d'un coup.

L'ancienne règle `kb-ls-p1-process-absent` utilisait une ancre **globale** (document le plus récent de tous
les hôtes) : un hôte dont seul l'agent était en retard déclenchait à tort. Elle faisait aussi 11 lignes au lieu
de 5.

### Réglages

| Réglage | Valeur | Raison |
|---|---|---|
| Fenêtre | 15 min | `last_ls = null` doit signifier « absent depuis 15 min », pas « filtré par la fenêtre » |
| `alert_delay` | 2 | un échantillon manqué ne déclenche pas. Délai réel de détection : 2 à 3 min. `alert_delay` 1 pour une détection stricte en 1 min (au prix d'un faux positif possible) |

**Message :**
`Logstash process not running on logstash-1 for more than 1 minute (agent still reporting, last host data 2026-10-26T21:15:25.734Z, last Logstash data: none in the last 15 min).`

Quand `last_ls` est null, une section Mustache inverse affiche `none in the last 15 min` :
`{{#_source.last_ls}}{{_source.last_ls}}{{/_source.last_ls}}{{^_source.last_ls}}none in the last 15 min{{/_source.last_ls}}`.

### Non couvert, volontairement

| Cas | Comportement | Couvert par |
|---|---|---|
| Serveur éteint ou agent arrêté | `last_seen` et `last_ls` s'arrêtent ensemble : pas d'alerte ; l'hôte sort de la fenêtre au bout de 15 min | `ls-node-metrics-missing`, état des agents dans Fleet |
| Logstash vivant mais pipeline en panne | process vu : pas d'alerte | `ls-pipe-not-running`, `ls-pipe-input-port` |
| JVM absent du top N de la collecte | faux positif possible | vérifier `include_top_n` dans la policy System (un JVM est en général dans le top mémoire) |
| Logstash ailleurs que sous `/DATA/logstash` ou hôte non nommé `logstash-*` | non surveillé | adapter les deux filtres |

---

## 6. Tests réalisés (26/10/2026, elk-remote)

| Test | Résultat |
|---|---|
| Exécution des 4 règles | `ok` / `succeeded`, 0 alerte (aucun seuil atteint : disque max 17,3 %, Logstash CPU et RAM < 6 %) |
| `ls-process-absent`, Logstash en marche (`/DATA/logstash*`) | 0 ligne |
| `ls-process-absent`, absence simulée (`/DATA/absent*`) | 2 lignes, logstash-1 et logstash-2, `last_ls = null` |
| Écriture dans `alerts-elklab` : copie temporaire de `ls-process-memory` au seuil `>= 0` | documents `firing`, `alert_id` = hôte, `warning`/`P2`, valeur affichée (5,31 % et 4,59 %) |
| Écriture dans `alerts-elklab` : copie temporaire de `ls-process-absent` sur `/DATA/absent*` | documents `firing`, `critical`/`P1`, message avec `none in the last 15 min` |

Les règles de test et leurs documents ont été supprimés du journal.

Pour rejouer un test : créer une copie de la règle avec un autre ID et un seuil forcé, `alert_delay` 1 et
`rule_id` = `test-…` dans l'action, attendre 1 à 2 min, lire `alerts-elklab`, puis supprimer la règle et
`POST alerts-elklab/_delete_by_query {"query":{"prefix":{"rule_id":"test-"}}}`.

---

## 7. Fichiers et déploiement

| Fichier | Contenu |
|---|---|
| `alerting/rules/<id>.json` | définition de la règle (requête, fenêtre, `alert_delay`, actions) |
| `alerting/alert-messages.json` | messages par règle et par groupe d'actions |
| `alerting/apply-messages.sh <id>` | régénère les actions `alerts-elklab` de la règle à partir des messages |
| `alerting/format-esql.py <fichier>` | met en forme la requête (un `|` par ligne, `BY` sur sa propre ligne) |

Création ou mise à jour d'une règle sur elk-remote (API Kibana 5602, utilisateur `elastic` de `remote/.env`) :

```
POST kbn:/api/alerting/rule/<id>                  corps : alerting/rules/<id>.json   (création, règle active)
PUT  kbn:/api/alerting/rule/<id>                  corps : le même sans rule_type_id ni consumer   (mise à jour)
POST kbn:/api/alerting/rule/<id>/_enable | _disable
```

`remote/deploy-alerting.sh` déploie **toutes** les règles du répertoire `alerting/rules/` sur elk-remote. Lancé
avec `enable`, il activerait aussi les anciennes règles désactivées : à éviter, ou les activer une par une.

---

## 8. Compléments recommandés

| Besoin | Règle à prévoir |
|---|---|
| Serveur ou agent qui ne remonte plus | absence de `metrics-system.*` par hôte (ou état des agents dans Fleet) |
| Saturation CPU du serveur | `metrics-system.cpu-*`, `system.cpu.total.norm.pct` ≥ 90 % |
| Heap Logstash saturé | `kb-ls-p1-heap` (`heap_used_percent`) |
| Disque critique | règle P1 séparée à 85-90 % |
| Inodes épuisés | `system.filesystem.free_files` |

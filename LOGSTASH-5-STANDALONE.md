# Logstash autonome (logstash-5) : configuration par fichiers

Instance Logstash **hors gestion centralisée Kibana et hors Fleet** : les pipelines sont définis dans des
fichiers `.conf` versionnés dans le dépôt (`logstash/standalone/`), poussés sur la VM et rechargés
automatiquement. Ce document couvre l'architecture, le déploiement, l'exploitation et le
**dimensionnement** (méthode de calcul + exemple chiffré).

---

## 1. Architecture

```
  sources syslog (serveurs, équipements réseau)
        │  TCP / UDP 5514
        ▼
┌──────────────────────────── logstash-5 ────────────────────────────┐
│  logstash-5-syslog_in              (IN)                            │
│    inputs tcp + udp 5514 → grok RFC 3164 / 5424 → syslog_pri → date │
│    file mémoire                                                    │
│        │  pipeline-to-pipeline (send_to, en mémoire)               │
│        ▼                                                           │
│  logstash-5-elasticsearch_out      (OUT)                           │
│    file PERSISTANTE (tampon en cas d'indisponibilité d'ES)         │
│    output elasticsearch, data_stream_auto_routing                  │
└────────────────────────────────────────────────────────────────────┘
        │  HTTPS, utilisateur logstash_internal (rôle logstash_writer)
        ▼
  es01 / es02 / es03  →  data stream  logs-syslog-standalone
```

Pourquoi deux pipelines :

| Pipeline | Rôle | File | Pourquoi |
|---|---|---|---|
| **IN** | réception + parsing (tout le coût CPU) | mémoire | rapide ; ne fait que transmettre à OUT |
| **OUT** | unique point d'écriture vers Elasticsearch | **persistante** | les événements survivent à un redémarrage de Logstash et à une coupure d'ES ; ajouter une nouvelle entrée (autre IN) ne touche pas à la sortie |

La destination est portée par l'événement (`data_stream.type / dataset / namespace` posés dans IN) :
OUT est générique et peut servir à d'autres pipelines IN.

### Différences avec logstash-1..4

| | logstash-1..4 (pools) | logstash-5 (autonome) |
|---|---|---|
| Définition des pipelines | Kibana > Logstash Pipelines (`xpack.management`) | fichiers `pipelines.yml` + `conf.d/*.conf` |
| Source de vérité | index `.logstash` dans ES | dépôt `logstash/standalone/` |
| Dépendance au démarrage | ES doit répondre (récupération des pipelines) | aucune (la file persistante tamponne) |
| Elastic Agent / Fleet | oui (policies par pool) | **non** (pas de métriques dans les règles `ls-*` ni Grafana) |
| Rechargement | Kibana → poll 5 s | `config.reload.automatic`, 10 s |

---

## 2. Fichiers

| Dépôt (`logstash/standalone/`) | Sur la VM (`/DATA/logstash/config/`) | Contenu |
|---|---|---|
| `logstash.yml.tmpl` | `logstash.yml` | nœud, chemins, rechargement auto, **sans** `xpack.management` |
| `pipelines.yml` | `pipelines.yml` | déclaration des 2 pipelines (workers, files) |
| `conf.d/syslog_in.conf` | `conf.d/syslog_in.conf` | pipeline IN |
| `conf.d/elasticsearch_out.conf` | `conf.d/elasticsearch_out.conf` | pipeline OUT |

Autres éléments posés par `logstash/deploy.sh` (communs à tous les Logstash du lab) :

| Élément | Emplacement |
|---|---|
| Binaires (tar.gz 9.5.4) | `/DATA/logstash` (filesystem XFS dédié, image loop 20 Go) |
| Keystore (`LS_WRITER_PASSWORD`, …) | `/DATA/logstash/config/logstash.keystore` |
| CA du lab | `/DATA/logstash/config/certs/ca.crt` |
| Pilotage | `startlogstash {start\|stop\|restart\|status}` (hors systemd, démarrage manuel) |
| Données / files persistantes | `/DATA/logstash/data/queue/<pipeline>` |
| Logs | `/DATA/logstash/logs/logstash-plain.log` |

### pipelines.yml (lab)

```yaml
- pipeline.id: logstash-5-syslog_in
  path.config: "/DATA/logstash/config/conf.d/syslog_in.conf"
  pipeline.workers: 1
  queue.type: memory

- pipeline.id: logstash-5-elasticsearch_out
  path.config: "/DATA/logstash/config/conf.d/elasticsearch_out.conf"
  pipeline.workers: 2
  queue.type: persisted
  queue.max_bytes: 256mb
```

Valeurs du lab (faible trafic). Pour la production, voir le [dimensionnement](#5-dimensionnement).

### Champs produits (data stream `logs-syslog-standalone`)

| Champ | Source |
|---|---|
| `@timestamp` | horodatage syslog (RFC 3164 sans fuseau : Europe/Paris supposé) |
| `message` | texte du message seul |
| `event.original` | ligne brute reçue |
| `host.hostname`, `host.ip` | émetteur |
| `process.name`, `process.pid` | APP et PID syslog |
| `log.syslog.priority`, `.facility.*`, `.severity.*` | PRI décodé |
| `network.transport` | `tcp` / `udp` |
| `pipeline.name` | `logstash-5-syslog_in` |
| `logstash.pool` | `standalone` |
| `tags: _grokparsefailure` | ligne non conforme RFC 3164/5424 (conservée telle quelle) |

---

## 3. Déploiement

```bash
# 1. VM Rocky Linux 10
orb create rocky:10 logstash-5

# 2. Installation + configuration (LS_STANDALONE, défaut « logstash-5 », pool « standalone »)
./logstash/deploy.sh install logstash-5
```

Le script, pour une VM autonome :

1. crée `/DATA` (XFS), installe Logstash, crée l'utilisateur `logstash` ;
2. écrit `logstash.yml` depuis `standalone/logstash.yml.tmpl`, copie `pipelines.yml` et `conf.d/*.conf` ;
3. crée le keystore, applique les droits, `startlogstash restart`.

`startlogstash` n'attend pas Elasticsearch au démarrage quand `xpack.management.elasticsearch.hosts` est
absent (mode autonome) : la file persistante de OUT absorbe une indisponibilité.

> logstash-5 n'est **pas** dans `LS_POOLS` (.env) : cette variable crée aussi les policies Fleet et pousse
> les pipelines dans Kibana. Les VM autonomes sont listées dans `LS_STANDALONE`.

### Modifier un pipeline

| Méthode | Effet |
|---|---|
| Modifier le fichier dans le dépôt puis `./logstash/deploy.sh install logstash-5` | **redémarre** Logstash (file persistante conservée) |
| Éditer `/DATA/logstash/config/conf.d/*.conf` sur la VM | rechargé en ≤ 10 s, **seul le pipeline modifié** redémarre ; à reporter ensuite dans le dépôt |

Une erreur de syntaxe lors d'un rechargement : l'ancien pipeline continue de tourner, l'erreur est dans
`logstash-plain.log`. Vérification à froid :

```bash
sudo -u logstash /DATA/logstash/bin/logstash --path.settings /DATA/logstash/config \
  -f /DATA/logstash/config/conf.d/syslog_in.conf --config.test_and_exit
```

---

## 4. Exploitation

### Commandes

```bash
sudo startlogstash status                                    # process + health report
curl -s localhost:9600/_health_report?pretty                  # green / yellow / red par pipeline
curl -s localhost:9600/_node/pipelines?pretty                 # pipelines chargés, workers, batch
curl -s localhost:9600/_node/stats/pipelines?pretty           # événements, flow metrics, files
ss -lntup | grep 5514                                         # écoute TCP + UDP
tail -f /DATA/logstash/logs/logstash-plain.log
```

### Test de bout en bout (depuis une autre VM)

```bash
orb -m logstash-1 logger --server logstash-5.orb.local --port 5514 --tcp --rfc3164 -t test "TEST-TCP"
orb -m logstash-1 logger --server logstash-5.orb.local --port 5514 --udp --rfc3164 -t test "TEST-UDP"
```

```
GET logs-syslog-standalone/_search?q=process.name:test&sort=@timestamp:desc
```

> Depuis le Mac, 5514 n'est pas joignable (« No route to host ») : tester depuis une VM ou un conteneur.

### Indicateurs à surveiller (`_node/stats/pipelines`)

| Indicateur | Normal | Signification si anormal |
|---|---|---|
| `events.in` − `events.out` (IN) | ≈ 0 | IN bloqué par OUT (contre-pression) |
| `queue.events_count` (OUT) | proche de 0 | > 0 durablement : ES lent ou injoignable, la file se remplit |
| `queue.queue_size_in_bytes` / `max_queue_size_in_bytes` (OUT) | < 50 % | à 100 % : IN est bloqué, **perte UDP** et TCP refusé |
| `flow.worker_utilization` | < 70 % à la pointe | proche de 100 % : manque de workers / CPU |
| `flow.queue_backpressure` | ≈ 0 | les inputs attendent : goulot en aval |
| tags `_grokparsefailure` (dans ES) | rare | format syslog non standard d'une source |

---

## 5. Dimensionnement

Méthode : **volume → événements/s → eps de pointe par instance → workers → CPU → batch → file → RAM/disque**,
puis **validation par la mesure**. Les hypothèses sont signalées ⚠ : à remplacer par des valeurs mesurées.

### 5.1 Données d'entrée

| Donnée | Symbole | Exemple |
|---|---|---|
| Volume journalier | V | 140 Go/jour |
| Nombre d'instances Logstash | N | 2 |
| Nombre de sources | S | 300 |
| Taille moyenne d'un événement brut ⚠ | e | 500 octets (syslog) |
| Facteur de pointe ⚠ | k | 3 |
| Débit d'un worker IN ⚠ (grok syslog + date + mutate) | w | 2 500 eps/cœur |
| Coupure d'ES à tolérer | T | 4 h |

Mesurer `e` : `GET _cat/indices/<index>?v&h=pri.store.size,docs.count` → taille primaire / nb de docs
(c'est la taille indexée ; la taille brute est de l'ordre de 40 à 60 % de celle-ci).

> Préciser ce que représente V : **flux entrant** ou **stockage ES**. Si c'est le stockage avec 1 réplica,
> le flux réel est environ deux fois plus faible.

### 5.2 Débit en événements par seconde

| Calcul | Formule | Exemple |
|---|---|---|
| Débit moyen (octets) | D = V / 86 400 | 140 Go / 86 400 ≈ **1,6 Mo/s** |
| eps moyen | eps_moy = D / e | 1,6 Mo/s / 500 o ≈ **3 250 eps** |
| eps de pointe | eps_pic = eps_moy × k | 3 250 × 3 ≈ **10 000 eps** |
| eps par source | eps_moy / S | ≈ 11 eps/source (en moyenne ; quelques sources bavardes font l'essentiel) |

### 5.3 Charge par instance

| Hypothèse | Formule | Exemple |
|---|---|---|
| **N+1 (recommandé)** : une instance absorbe tout pendant une panne ou un upgrade | eps_inst = eps_pic | **10 000 eps** |
| Sans redondance | eps_inst = eps_pic / N | 5 000 eps |

Répartition de charge obligatoire devant les instances (syslog ne la fait pas) : VIP keepalived, HAProxy
(TCP) ou DNS round-robin.

### 5.4 Workers et CPU

```
workers_IN  = ceil(eps_inst / w)
workers_OUT = 2 à 4          (sortie ES : limitée par le réseau / ES, peu de CPU ; = nb de bulks parallèles)
vCPU        = workers_IN + workers_OUT / 2 + 2      (+2 : inputs TCP/UDP, GC, système)
```

| Calcul | Exemple |
|---|---|
| workers_IN = ceil(10 000 / 2 500) | **4** |
| workers_OUT | **4** |
| vCPU = 4 + 4/2 + 2 | **8** |

### 5.5 Batch size et batch delay

Un worker n'attend pas que le batch soit plein : il part dès que **batch.size** est atteint **ou** que
**batch.delay** est écoulé.

```
batch_effectif = min( batch.size , eps_inst / workers × batch.delay )
```

| Pipeline | Calcul à la pointe | Conclusion |
|---|---|---|
| IN, 4 workers, delay 50 ms | 10 000 / 4 × 0,05 = **125** événements | un batch.size > 250 ne sert à rien ; garder 250 / 50 ms (latence faible) |
| OUT, 4 workers, delay 200 ms | 10 000 / 4 × 0,2 = **500** événements | 1 batch OUT = 1 requête `_bulk` |

Taille d'un bulk = batch_effectif × taille du document JSON enrichi (≈ 1,5 Ko ⚠) :
500 × 1,5 Ko ≈ **0,75 Mo** → dans la cible **1 à 10 Mo** (plafond du plugin : 20 Mo par requête).
Augmenter `batch.delay` de OUT grossit les bulks (moins de requêtes vers ES) au prix d'un peu de latence,
sans importance pour des logs.

Mémoire des événements en vol :

```
en_vol = Σ (workers × batch.size)  =  4 × 250 + 4 × 1 000  =  5 000 événements
RAM    ≈ en_vol × taille_doc × 10 (surcoût objets Java)  ≈  5 000 × 1,5 Ko × 10  ≈  75 Mo
```

→ négligeable : ce n'est pas ce qui dimensionne le heap.

### 5.6 File persistante (OUT)

```
queue.max_bytes = D × T × 1,3 (surcoût de sérialisation)
```

| Calcul | Exemple |
|---|---|
| Coupure ES de 4 h : 1,6 Mo/s × 14 400 s × 1,3 | ≈ **30 Go** au total |
| En N+1 (une instance reçoit tout) | **30 Go par instance** |
| Sans redondance | 15 Go par instance |

- `queue.page_capacity` : 64 Mo (défaut) en production (16 Mo dans le lab).
- File pleine → IN bloqué → **perte UDP**, connexions TCP en attente : dimensionner T sur la pire coupure
  réaliste (maintenance ES, rolling upgrade).
- La file persistante coûte 10 à 30 % de débit et écrit en continu : **disque SSD**.

### 5.7 Entrées : 300 sources

| Protocole | Réglage |
|---|---|
| TCP | 300 connexions : aucun réglage particulier |
| UDP (risque de perte en rafale) | input `udp` : `workers => 4`, `queue_size => 10000`, `receive_buffer_bytes => 33554432` ; noyau : `net.core.rmem_max = 33554432` |

Pertes UDP visibles avec `netstat -su` (`receive buffer errors`). Privilégier TCP pour les sources qui le
permettent.

### 5.8 RAM, heap, disque, réseau

| Ressource | Formule / règle | Exemple |
|---|---|---|
| Heap JVM | 4 à 8 Go, `-Xms` = `-Xmx`, ≤ 50 % de la RAM | **6 Go** |
| RAM VM | heap × 2 minimum (page cache de la file persistante, OS) | **16 Go** |
| Disque /DATA | queue.max_bytes + logs + 50 % de marge | **SSD 100 Go** |
| Réseau | entrée D, sortie ≈ 2 × D (JSON enrichi) | 1,6 Mo/s in, ≈ 3 Mo/s out → 1 Gbit/s largement suffisant |

### 5.9 Résultat pour l'exemple (140 Go/jour, 2 instances, 300 sources, N+1)

**VM (par instance)** : 8 vCPU, 16 Go RAM (heap 6 Go), SSD 100 Go pour `/DATA`, 1 Gbit/s.

`pipelines.yml` :

```yaml
- pipeline.id: logstash-5-syslog_in
  path.config: "/DATA/logstash/config/conf.d/syslog_in.conf"
  pipeline.workers: 4
  pipeline.batch.size: 250
  pipeline.batch.delay: 50
  pipeline.ordered: false          # 4 workers : l'ordre strict n'a pas de sens et ralentit
  queue.type: memory

- pipeline.id: logstash-5-elasticsearch_out
  path.config: "/DATA/logstash/config/conf.d/elasticsearch_out.conf"
  pipeline.workers: 4
  pipeline.batch.size: 1000
  pipeline.batch.delay: 200
  queue.type: persisted
  queue.max_bytes: 30gb
  queue.page_capacity: 64mb
```

`jvm.options` : `-Xms6g` / `-Xmx6g` (variable `LS_HEAP=6g` de `logstash/deploy.sh`).

Input UDP (`syslog_in.conf`) :

```
udp {
  host => "::"
  port => 5514
  workers => 4
  queue_size => 10000
  receive_buffer_bytes => 33554432
  add_field => { "[network][transport]" => "udp" }
}
```

Système : `sysctl -w net.core.rmem_max=33554432` (persistant dans `/etc/sysctl.d/`).

### 5.10 Valider par la mesure

Les valeurs ⚠ sont des points de départ. Faire un test de charge réaliste (rejeu de vrais logs) puis
ajuster à partir de `_node/stats/pipelines` :

| Mesure | Cible | Action si dépassée |
|---|---|---|
| `flow.worker_utilization` IN | < 70 % à la pointe | + workers IN et + vCPU |
| `flow.queue_backpressure` IN | ≈ 0 | goulot en aval : OUT ou ES |
| `flow.worker_utilization` OUT élevée + latence bulk élevée | — | le goulot est **Elasticsearch** (shards primaires du data stream, `refresh_interval`, nœuds hot) |
| `queue.events_count` OUT croissant en régime normal | 0 | ES n'absorbe pas le débit |

Recalcul : `w_mesuré = eps_inst_test / (workers_IN × worker_utilization)` puis
`workers_IN = ceil(eps_inst / w_mesuré)`.

Au-delà d'environ 10 000 eps, le goulot est généralement **Elasticsearch** (nombre de shards primaires,
`refresh_interval`), pas les Logstash.

---

## 6. Points d'attention

- **Pas de monitoring Fleet** : logstash-5 n'apparaît ni dans les règles Kibana `ls-*` ni dans Grafana.
  Pour l'y ajouter : enrôler un Elastic Agent avec l'intégration Logstash (metrics `:9600`).
- **Démarrage manuel** : pas de relance au boot ni après crash (`startlogstash start`).
- **Ports < 1024** : Logstash ne tourne pas en root ; utiliser 5514 (ou une redirection iptables 514 → 5514).
- **Horodatage RFC 3164** sans fuseau : Europe/Paris supposé ; à adapter si des sources sont en UTC.
- **Une seule source de vérité** : toute modification faite à chaud sur la VM doit être reportée dans
  `logstash/standalone/`, sinon elle est écrasée au prochain `deploy.sh install`.

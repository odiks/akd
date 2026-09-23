# Elastic Agent en tant que collecteur OpenTelemetry (OTel Collector)

> Résumé de la documentation Elastic : https://www.elastic.co/docs/reference/fleet/elastic-agent-as-otel-collector

## 1. Contexte et objectif

À partir de la **version 9.2**, Elastic Agent embarque un **OTel Collector** directement dans son processus. Auparavant, Elastic Agent agissait comme un simple superviseur qui lançait et gérait des sous-processus Beats indépendants (Filebeat, Metricbeat, etc.), chacun tournant séparément.

Avec la nouvelle architecture :
- Elastic Agent **intègre un OTel Collector comme runtime**, ce qui élimine la surcharge liée à la gestion de sous-processus séparés.
- Les inputs Beats peuvent désormais tourner **à l'intérieur** du Collector OTel, sous forme de **Beat receivers**.
- Elastic Agent s'appuie sur les receivers et pipelines OTel pour ingérer, transformer et exporter la télémétrie de façon unifiée, dans un seul processus.
- Des receivers **natifs OTel** peuvent tourner **dans le même Collector**, aux côtés des Beat receivers.
- La **rétrocompatibilité est préservée** : les intégrations Beats existantes continuent de fonctionner via les Beat receivers, sans changement de configuration ni changement des données collectées.

⚠️ **Transition incrémentale** : en 9.2, seul le **self-monitoring de l'agent** (métriques/logs sur lui-même) utilise le runtime OTel par défaut. Les **inputs de collecte de données** seront migrés vers des receivers OTel progressivement, au fil des prochaines versions. Les configurations d'agents et intégrations existantes continuent de fonctionner sans interruption.

Le composant qui implémente les intégrations basées sur Beats est nommé `elastic-otel-collector`.

---

## 2. Vue d'ensemble de l'architecture

**Avant** : Elastic Agent = superviseur → lance des processus Beats séparés (Filebeat, Metricbeat...) chacun dans son propre process.

**Maintenant** : Elastic Agent = un seul processus OTel Collector, qui héberge :
- des **Beat receivers** (wrapping des inputs Beats)
- des **receivers OTel natifs**

Cela réduit l'empreinte mémoire/CPU de l'agent (plus de multiplication de sous-processus).

---

## 3. Les Beat receivers

Un **Beat receiver** est un input Beat (ex: `filestream`) et ses processeurs associés, encapsulés pour fonctionner comme un receiver OTel à l'intérieur du Collector.

**Points clés :**
- Les Beat receivers produisent **exactement les mêmes données**, au format **ECS (Elastic Common Schema)**, que les inputs Beats classiques.
- Les Beat receivers **ne produisent pas** de données au format OTLP — ils restent au format ECS.
- Quand les Beat receivers sont activés, Elastic Agent **traduit automatiquement** les parties pertinentes de son fichier `elastic-agent.yml` (standalone ou généré par Fleet) en configuration de Collector OTel.

**Chemin de la donnée (data path) :**
1. Un input Beat (ex: `filestream`) collecte la donnée.
2. Des processeurs spécifiques à Beats transforment la donnée.
3. La donnée passe par le traitement OTel.
4. Un exporter (Elasticsearch, Logstash, ou Kafka) écrit la donnée vers la destination.
5. Comme avec Beats et Elastic Agent classiques, la donnée peut être traitée par des **ingest pipelines** avant d'être stockée dans Elasticsearch.

⚠️ Le support des outputs pour les Beat receivers **varie selon la version** de l'Elastic Agent (voir section compatibilité ci-dessous).

### 3.1 Compatibilité de configuration

L'introduction des Beat receivers **ne nécessite aucun changement de configuration**. Inputs et outputs restent identiques pour les données d'intégration. La migration se déroule de façon incrémentale selon les versions :

- Les données de self-monitoring de l'agent (métriques et logs) utilisent les Beat receivers par défaut, avec l'output Elasticsearch.
- Certains inputs de métriques utilisent les Beat receivers par défaut, avec l'output Elasticsearch (liste précise dans les release notes de l'Elastic Agent 9.3.0).
- À terme, **tous les inputs de métriques** utiliseront les Beat receivers, avec support des outputs Elasticsearch, Logstash **et** Kafka.

**Agents gérés par Fleet (Fleet-managed) :**
- Les mêmes packages d'intégration basés sur Beats continuent de fonctionner.
- Ces packages configurent automatiquement les Beat receivers correspondants.
- Les assets (dashboards, alertes, ingest pipelines) restent **inchangés**.

**Agents standalone :**
- Les configurations standalone existantes sont acceptées telles quelles.
- Elastic Agent génère en interne la configuration du Collector OTel correspondante.

---

## 4. Elastic Agent avec plusieurs méthodes de collecte simultanées

Elastic Agent peut collecter des données via **deux méthodes en parallèle**, dans le **même processus** OTel Collector :

1. **Collecte basée sur Beats** : l'agent utilise des inputs/receivers Beats pour collecter des données au format ECS.
2. **Collecte native OTel** : l'agent utilise des receivers OTel Collector standards pour ingérer de la télémétrie via OTLP, en suivant les conventions sémantiques OTel.

Les deux méthodes tournent **dans le même processus**, ce qui permet de combiner collecte Beats traditionnelle et collecte native OTel **au sein d'une seule instance d'agent**, plutôt que de devoir utiliser des outils séparés. Cela réduit la consommation mémoire par rapport à l'exécution de sous-processus Beats séparés en parallèle d'un Collector OTel autonome.

En pratique, un même fichier `elastic-agent.yml` peut contenir :
- une section `inputs` et `outputs` pour la collecte basée sur Beats,
- des sections `receivers`, `exporters` et `service.pipelines` pour la collecte basée sur OTel.

### Exemple de configuration hybride

Cet exemple combine :
- la collecte de logs d'authentification système via un input Beat (`filestream`),
- le monitoring d'un endpoint HTTP via un receiver OTel (`httpcheck`).

```yaml
inputs:
  - id: filestream-system-66cab0a6-6fa3-46b1-9af1-2ea171fbd885
    type: filestream
    data_stream:
      namespace: default
    streams:
      - id: filestream-system.auth-66cab0a6-6fa3-46b1-9af1-2ea171fbd885
        data_stream:
          dataset: system.auth
        paths:
          - /var/log/auth*.log

outputs:
  default:
    type: elasticsearch
    hosts: [127.0.0.1:9200]
    api_key: "your-api-key"

receivers:
  httpcheck/httpcheck-6d24bb0d-5349-4714-a7ea-2088abcb928b:
    collection_interval: 30s
    targets:
      - method: "GET"
        endpoints:
          - https://example.com

exporters:
  elasticsearch/default:
    endpoints: [127.0.0.1:9200]
    api_key: "your-api-key"

service:
  pipelines:
    metrics/httpcheck-6d24bb0d-5349-4714-a7ea-2088abcb928b:
      receivers: [httpcheck/httpcheck-6d24bb0d-5349-4714-a7ea-2088abcb928b]
      exporters: [elasticsearch/default]
```

---

## 5. Intégrations OpenTelemetry (packages)

Le catalogue d'intégrations propose des packages qui regroupent configuration applicative et assets. Pour les intégrations classiques basées sur ECS, un package inclut une configuration d'agent + des assets Elasticsearch/Kibana (dashboards, alertes, ingest pipelines).

Une approche similaire existe pour la collecte native OTel, répartie en deux types de packages :

- **OpenTelemetry input packages** : contiennent la configuration nécessaire pour le receiver OTel et les composants de pipeline associés.
- **Content packages** : contiennent les assets correspondants (dashboards, visualisations, etc.) pour l'application dont la donnée est ingérée via le receiver OTel.

**Fonctionnement :**
- Une **même politique d'agent** (agent policy) peut inclure à la fois des intégrations basées sur ECS et des packages OpenTelemetry input.
- Quand on ajoute un package OpenTelemetry input à une agent policy, cela configure la section receiver OTel de la configuration Elastic Agent.
- Une fois la donnée ingérée dans Elasticsearch, les assets OTel correspondants sont **automatiquement installés** (quand disponibles).

💡 Le même déploiement automatique d'assets s'applique en mode Elastic Agent **standalone** : dès que la donnée est ingérée via le Collector, cela déclenche l'installation automatique des assets OTel pertinents (quand disponibles).

---

## 6. Comparatif des types de collecteurs

Selon l'environnement, on peut utiliser :
- Elastic Agent (Fleet-managed),
- Elastic Agent standalone en mode OTel,
- ou un Collector OpenTelemetry tiers, pour envoyer des données OTel vers Elastic.

| Collector | Monitoring central Fleet | Gestion centrale Fleet | Beat receivers | Logstash exporter | Elastic Defend | Cloud Security | Profiler |
|---|---|---|---|---|---|---|---|
| **Elastic Agent (Fleet-managed)** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Elastic Agent (standalone)** | Planifié (roadmap) | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ |
| **Collector OTel tiers (upstream)** | Planifié (roadmap) | Planifié (roadmap) | ❌ | ❌ | ❌ | ❌ | ❌ |

*"Planifié" signifie que le support est sur la roadmap mais pas encore disponible en GA.*

📌 Un Elastic Agent standalone peut s'enrôler dans Fleet sur le terrain si une bascule vers le mode Fleet-managed est nécessaire ultérieurement. En revanche, un **Elastic Agent standalone en mode OTel ne supporte pas** l'enrôlement dans Fleet.

---

## 7. FAQ

**Les données collectées par Elastic Agent passent-elles par des ingest pipelines ?**
Cela dépend du receiver qui a collecté la donnée :
- Donnée collectée par un **Beat receiver** → écrite via l'exporter Elasticsearch → passe par les **ingest pipelines**, est schématisée et stockée dans le data stream approprié (comme avec Elastic Agent/Beats traditionnels).
- Donnée collectée par un **receiver OTel natif** → suit les conventions sémantiques OpenTelemetry → **contourne les ingest pipelines** et est stockée directement dans un data stream spécifique OTel.

**Peut-on configurer des Beat receivers sur un Elastic Agent standalone ?**
Oui. Il faut fournir une configuration de Collector OTel standard et configurer manuellement les Beat receivers comme `filebeatreceiver` ou `metricbeatreceiver`. Exemple :

```yaml
receivers:
    filebeatreceiver:
        filebeat:
            inputs:
                - data_stream:
                    dataset: generic
                  id: filestream-receiver
                  index: logs-generic-default
                  paths:
                    - /var/log/*.log
                  type: filestream
    metricbeatreceiver:
        metricbeat:
            modules:
                - data_stream:
                    dataset: system.cpu
                  index: metrics-system.cpu-default
                  metricsets:
                    - cpu
                  module: system
```

**Quelles sont les différences entre Elastic Agent Fleet-managed, Elastic Agent standalone, et un Collector OpenTelemetry tiers ?**
La principale différence porte sur la **gestion**. Elastic Agent peut être géré par Fleet en mode Fleet-managed. L'Elastic Agent standalone en mode OTel ne supporte ni l'enrôlement Fleet, ni la gestion centralisée. Certaines fonctionnalités, comme **Elastic Defend**, ne sont disponibles que sous gestion Fleet et ne peuvent pas être utilisées avec un Elastic Agent standalone seul.

**Quel est l'avenir de l'Elastic Agent en mode standalone ?**
Les cas d'usage standalone peuvent désormais être couverts par l'Elastic Agent en mode OTel. Un avantage pratique de l'Elastic Agent standalone actuel est qu'il peut être **mis à niveau vers Fleet-managed sur le terrain**, sans réinstallation.

**Les données collectées par des Collectors OTel tiers peuvent-elles déclencher l'installation automatique d'assets ?**
Non. L'installation automatique d'assets à partir des content packages OTel fonctionne **uniquement** pour les données ingérées via **Elastic Agent**. Les données ingérées via un Collector OpenTelemetry tiers **ne déclenchent pas** l'installation automatique d'assets.

**Y aura-t-il une matrice de support OS distincte ?**
Non, la matrice de support système d'Elastic ne change pas. Là où Elastic ne fournit pas de support Elastic Agent pour un OS spécifique, on peut déployer un Collector OpenTelemetry tiers supporté par le fournisseur concerné et envoyer les données vers Elastic (ex: Red Hat fournit un Collector OTel pour OpenShift configurable pour envoyer des données vers Elastic). La même configuration peut aussi être utilisée avec Elastic Agent sur les OS supportés par Elastic.

---

## 8. Points clés à retenir (synthèse)

| Aspect | À retenir |
|---|---|
| **Version** | Disponible à partir de la 9.2 |
| **Architecture** | Un seul processus OTel Collector au lieu de sous-processus Beats séparés |
| **Beat receivers** | Format ECS préservé, pas de rupture de compatibilité, données via ingest pipelines |
| **Receivers OTel natifs** | Format OTLP/conventions sémantiques, **bypass des ingest pipelines**, data stream OTel dédié |
| **Coexistence** | Beats + OTel natif dans le même agent/processus, via `inputs`/`outputs` ET `receivers`/`exporters`/`pipelines` dans le même YAML |
| **Fleet-managed** | Migration transparente, assets inchangés, seul le runtime change en coulisses |
| **Standalone** | Config existante acceptée telle quelle ; Beat receivers configurables manuellement (`filebeatreceiver`, `metricbeatreceiver`) ; pas d'enrôlement Fleet possible en mode OTel |
| **Limite importante** | Elastic Defend, Cloud Security et Profiler nécessitent Fleet-managed — indisponibles en standalone ou avec un Collector tiers |
| **Assets auto-installés** | Uniquement si la donnée passe par Elastic Agent (pas par un Collector tiers) |

---

*Document généré à partir de la documentation officielle Elastic (dernière consultation : 23 septembre 2026). Pour toute mise à jour, se référer à la page source.*

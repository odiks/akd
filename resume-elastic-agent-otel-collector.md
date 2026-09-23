# Elastic Agent comme collecteur OpenTelemetry — résumé détaillé

> Source : [Elastic Agent as an OpenTelemetry Collector](https://www.elastic.co/docs/reference/fleet/elastic-agent-as-otel-collector), documentation Elastic consultée le 23 septembre 2026.

## Idée principale

À partir d'Elastic Agent **9.2**, Elastic Agent embarque un runtime OpenTelemetry Collector (OTel Collector). L'objectif n'est pas de remplacer brutalement les intégrations Elastic historiques : Elastic fait évoluer progressivement l'Agent qui, auparavant, lançait plusieurs sous-processus distincts (par exemple Filebeat et Metricbeat), vers un unique processus basé sur OTel.

Cette évolution apporte les capacités, les pipelines et l'interopérabilité de l'écosystème OpenTelemetry tout en préservant les intégrations Beats, les configurations existantes et le format des données ECS déjà utilisés par Elastic.

## Avant et après

| Avant | Nouvelle architecture |
|---|---|
| Elastic Agent supervise plusieurs processus Beats séparés. | Elastic Agent embarque un OTel Collector comme runtime. |
| Chaque Beat collecte et traite ses propres données. | Les collecteurs Beats deviennent des *Beat receivers* exécutés dans le Collector. |
| OTel nécessite généralement un Collector séparé. | Les receivers OTel natifs et les Beat receivers peuvent cohabiter dans le même processus. |
| Plusieurs processus impliquent davantage de gestion et de consommation mémoire. | Un pipeline unifié réduit l'empreinte par rapport à plusieurs sous-processus indépendants. |

Le composant qui porte les intégrations fondées sur Beats dans cette architecture s'appelle `elastic-otel-collector`.

## Les Beat receivers : compatibilité avec les intégrations existantes

Un **Beat receiver** correspond à un input Beat et à ses processeurs associés, encapsulés pour être exécutés comme receiver OTel.

Points essentiels :

- Il produit les mêmes données que l'input Beat traditionnel, au format **Elastic Common Schema (ECS)**.
- Il ne convertit pas ces données en schéma OTLP : les données restent ECS.
- Pour un Agent géré par Fleet, les packages d'intégration existants continuent de fonctionner ; Fleet configure automatiquement les Beat receivers nécessaires.
- Pour un Agent standalone, les configurations existantes restent acceptées. Elastic Agent génère la configuration OTel interne correspondante.
- Les dashboards, règles d'alerte, ingest pipelines et autres assets des intégrations restent inchangés.

Le chemin de données d'un Beat receiver est le suivant :

```text
Input Beat (ex. filestream)
  → processeurs spécifiques au Beat
  → traitements OTel
  → exporter (Elasticsearch, Logstash ou Kafka)
  → ingest pipeline Elasticsearch, si applicable
  → data stream Elastic
```

Le support des sorties dépend de la version d'Elastic Agent. La migration est incrémentale : l'auto-monitoring de l'Agent utilise déjà ce runtime par défaut en 9.2, puis les inputs de métriques et d'autres inputs migrent progressivement. Il faut donc vérifier les notes de version de la version d'Agent réellement déployée avant de s'appuyer sur une sortie ou un input spécifique.

## Deux modes de collecte dans un seul Agent

Un même Elastic Agent peut combiner simultanément :

1. **La collecte ECS / Beats** : via les inputs et intégrations Elastic traditionnels, exécutés via Beat receivers.
2. **La collecte OTel native** : via les receivers standard du Collector OpenTelemetry, qui reçoivent ou interrogent des données OTLP conformes aux conventions sémantiques OTel.

Les deux modes s'exécutent dans le même processus OTel Collector. Il n'est donc plus nécessaire d'exploiter, sur le même hôte, un Elastic Agent/Beat pour les données Elastic et un Collector OTel indépendant pour les données OTel natives.

Une configuration peut ainsi posséder en parallèle :

- les sections `inputs` et `outputs` pour les sources Beats ;
- les sections `receivers`, `exporters` et `service.pipelines` pour les sources OTel natives.

Exemple conceptuel : un input `filestream` lit les logs système et les envoie au format ECS, pendant qu'un receiver OTel `httpcheck` teste périodiquement une URL et exporte des métriques OTel vers Elasticsearch.

## Différence fondamentale : ECS et OTLP ne suivent pas le même chemin

Cette distinction est importante pour concevoir les traitements et les recherches :

| Source | Modèle de données | Ingest pipelines Elasticsearch |
|---|---|---|
| Beat receiver | ECS | Oui : comportement identique aux Beats / Elastic Agent traditionnels. |
| Receiver OTel natif | Conventions sémantiques OpenTelemetry / OTLP | Non : les données sont stockées directement dans des data streams spécifiques à OTel. |

Autrement dit, un pipeline d'ingestion Elastic existant ne doit pas être supposé applicable aux données émises par un receiver OTel natif. Les enrichissements doivent plutôt être réalisés dans le pipeline OTel, par la configuration de l'intégration OTel, ou en tenant compte du modèle OTel à la destination.

## Intégrations OpenTelemetry dans Fleet

Le catalogue d'intégrations distingue deux catégories complémentaires :

- Les intégrations ECS traditionnelles : configuration Agent + assets Elasticsearch/Kibana, tels que dashboards, alertes et ingest pipelines.
- Les packages d'input OpenTelemetry : configuration du receiver OTel et des composants de pipeline associés.

Des **content packages** peuvent fournir les assets associés aux données OTel : dashboards, visualisations et autres contenus d'observabilité.

Une même policy Fleet peut contenir à la fois des intégrations ECS et des packages d'input OTel. Lorsqu'un package OTel est ajouté, Fleet renseigne la section de receiver OTel dans la configuration de l'Agent. Si des assets OTel sont disponibles, ils sont installés automatiquement après l'ingestion des données.

La documentation précise que ce déploiement automatique d'assets fonctionne aussi pour Elastic Agent standalone lorsque les données sont collectées par son Collector intégré. En revanche, les données envoyées par un Collector OTel tiers ne déclenchent pas cette installation automatique.

## Comparaison des options de déploiement

| Capacité | Elastic Agent géré par Fleet | Elastic Agent standalone | Collector OTel upstream / tiers |
|---|---:|---:|---:|
| Monitoring centralisé Fleet | Oui | Prévu | Prévu |
| Gestion centrale Fleet | Oui | Non | Prévu |
| Beat receivers | Oui | Oui | Non |
| Export Logstash | Oui | Oui | Non |
| Elastic Defend | Oui | Non | Non |
| Cloud Security | Oui | Non | Non |
| Profiler | Oui | Non | Non |

Dans cette table, « Prévu » signifie que la fonctionnalité est sur la feuille de route et n'est pas encore généralement disponible.

### Conséquence pratique

- Choisir **Elastic Agent géré par Fleet** lorsque l'on souhaite centraliser les policies, les intégrations Elastic, la sécurité Elastic et les données OTel dans un même outil.
- Choisir **Elastic Agent standalone** lorsqu'une configuration autonome est requise tout en conservant les Beat receivers et les capacités OTel. L'Agent standalone peut ensuite être enrôlé dans Fleet sur le terrain si une migration vers Fleet-managed est nécessaire.
- Choisir un **Collector OTel tiers** lorsque la plateforme ou le fournisseur le requiert. En contrepartie, les Beat receivers, les fonctions Elastic spécifiques et l'installation automatique des assets Elastic ne sont pas disponibles.

Un Elastic Agent exécuté directement en « OTel mode » n'est pas enrôlable dans Fleet ; ce point est distinct de l'Elastic Agent standalone classique que l'on peut ultérieurement faire évoluer vers un mode Fleet-managed.

## Exemple de structure de configuration hybride

Le principe illustré par Elastic est le suivant :

```yaml
# Collecte historique ECS / Beat
inputs:
  - type: filestream
    streams:
      - data_stream:
          dataset: system.auth
        paths:
          - /var/log/auth*.log

outputs:
  default:
    type: elasticsearch
    hosts: ["127.0.0.1:9200"]

# Collecte native OTel
receivers:
  httpcheck/example:
    collection_interval: 30s
    targets:
      - method: GET
        endpoints: ["https://example.com"]

exporters:
  elasticsearch/default:
    endpoints: ["127.0.0.1:9200"]

service:
  pipelines:
    metrics/httpcheck/example:
      receivers: [httpcheck/example]
      exporters: [elasticsearch/default]
```

Ce n'est pas une configuration complète de production : elle illustre surtout la coexistence de la syntaxe Beats historique et de la syntaxe standard OTel dans le même fichier.

## Points d'attention pour un projet de migration

1. **Migration progressive, pas de réécriture immédiate.** Les configurations et intégrations Beats sont préservées. Il n'est pas nécessaire de convertir immédiatement des intégrations ECS vers OTLP.
2. **Vérifier la version.** La disponibilité exacte des Beat receivers et des sorties dépend de la version d'Elastic Agent.
3. **Ne pas confondre les schémas.** ECS est conservé pour les Beat receivers ; les receivers OTel natifs suivent les conventions OTel.
4. **Revoir les traitements.** Les ingest pipelines Elasticsearch s'appliquent aux données des Beat receivers, mais pas aux données collectées par un receiver OTel natif.
5. **Éviter les doublons.** Une source ne doit pas être collectée une fois par une intégration Beat et une seconde fois par un receiver OTel, sauf besoin explicite.
6. **Préserver Fleet pour les fonctions Elastic avancées.** Elastic Defend, Cloud Security et le Profiler nécessitent le mode Fleet-managed selon la comparaison d'Elastic.
7. **Choisir le Collector selon l'exploitation.** Le Collector upstream est plus neutre dans un environnement multi-fournisseurs ; Elastic Agent offre une meilleure intégration avec les assets, Fleet et les fonctionnalités Elastic.

## Conclusion

Elastic Agent devient un point de collecte unifié : il peut faire fonctionner les intégrations Elastic historiques et les pipelines OpenTelemetry natifs dans un même runtime. La valeur principale de cette architecture est la continuité : les utilisateurs gardent leurs données ECS, leurs packages Fleet et leurs assets existants, tout en pouvant adopter progressivement les receivers et conventions OpenTelemetry.

Pour une organisation déjà équipée de Fleet et d'intégrations Elastic, le chemin le moins risqué est généralement de conserver les sources Beats existantes puis d'introduire OTel pour les nouvelles sources ou les cas nécessitant des receivers OTel natifs. Pour une architecture purement OTel ou multi-vendeur, le choix entre Elastic Agent standalone et un Collector upstream dépendra surtout des besoins de gestion centralisée et des fonctions Elastic spécifiques.

# Rapport de migration : supervision Zabbix vers Elastic Agent et Kibana Alerting

| | |
|---|---|
| **Version** | 0.1 (brouillon) |
| **Date** | 2026-11-11 |
| **Auteur** | [À COMPLÉTER] |
| **Statut** | En rédaction / En relecture / Validé |
| **Diffusion** | [À COMPLÉTER : équipes exploitation, responsables applicatifs, management] |

**Historique des versions**

| Version | Date | Auteur | Modification |
|---|---|---|---|
| 0.1 | 2026-11-11 | [À COMPLÉTER] | Création du document |

> Les passages entre crochets `[À COMPLÉTER]` sont à remplir avec les chiffres et décisions issus du fichier Excel et des tests. Les textes en italique sont des consignes de rédaction, à supprimer dans la version finale.

---

## 1. Synthèse

**[N] alertes Zabbix ont été inventoriées. [X + Y] sont désormais couvertes par des règles Kibana, [~10] ne sont pas migrables et [Z] sont abandonnées.** La supervision peut basculer sur Elastic une fois la période de fonctionnement en parallèle validée et le sort des alertes non migrables décidé.

| Statut | Nombre | % | Dont critiques |
|---|---|---|---|
| Migrée à l'identique | [X1] | [%] | [ ] |
| Migrée avec adaptation | [X2] | [%] | [ ] |
| Remplacée par une autre approche | [X3] | [%] | [ ] |
| **Non migrable** | **[~10]** | [%] | [ ] |
| Abandonnée (obsolète, doublon, jamais déclenchée) | [Z] | [%] | [ ] |
| **Total** | **[N]** | 100 % | [ ] |

**Décisions attendues**

1. Sort de chacune des [~10] alertes non migrables (section 9) : alternative, maintien temporaire de Zabbix, ou acceptation du risque.
2. Validation de la date de bascule : [À COMPLÉTER].
3. Validation du décommissionnement de Zabbix et des agents Zabbix : [À COMPLÉTER].

---

## 2. Contexte et objectifs

**Contexte.** La supervision et l'alerting reposent aujourd'hui sur Zabbix (serveur et Zabbix agent sur [N] hôtes). La plateforme Elastic est déjà utilisée pour [À COMPLÉTER : logs, métriques, Stack Monitoring…]. La migration regroupe collecte, stockage et alerting sur une seule plateforme.

**Objectifs**

| Objectif | Indicateur | Cible |
|---|---|---|
| Couvrir les alertes existantes | Part des alertes Zabbix couvertes ou abandonnées avec justification | [ ] % |
| Ne pas dégrader la détection | Délai entre l'incident et l'alerte | ≤ délai Zabbix actuel |
| Réduire le bruit | Nombre de faux positifs pendant le double run | < Zabbix |
| Supprimer un outil | Zabbix décommissionné | [date] |

---

## 3. Périmètre

**Référentiel.** L'inventaire de départ est le fichier `[nom du fichier].xlsx`, version du [date], fourni par [équipe / personne]. Chaque ligne de ce fichier a un statut dans la matrice de correspondance (section 7 et annexe A).

| Élément | Valeur |
|---|---|
| Hôtes supervisés | [N] |
| Systèmes | [Linux / Windows / équipements réseau…] |
| Templates Zabbix | [liste ou nombre] |
| Triggers (alertes) inventoriés | [N] |
| Media et destinataires | [email, SMS, ITSM, chat…] |

**Hors périmètre**

- Dashboards et graphes Zabbix (traités séparément : [À COMPLÉTER]).
- Historique des métriques Zabbix : non repris.
- [À COMPLÉTER]

---

## 4. Correspondance des concepts Zabbix → Elastic

| Zabbix | Elastic | Remarque |
|---|---|---|
| Zabbix agent | Elastic Agent, géré par Fleet | Un seul agent pour métriques, logs et Osquery |
| Template | Intégration + agent policy | L'agent policy décide quels hôtes collectent quoi |
| Host group | Agent policy, tags | |
| Item | Champ ou métrique dans un data stream | Ex. `system.cpu.total.norm.pct` |
| Trigger (expression) | Règle Kibana (ES\|QL, custom threshold, Synthetics…) | Une règle peut couvrir tous les hôtes à la fois |
| Sévérité | Priorité (P1, P2…) et `severity` | Table de correspondance en section 6 |
| Macro (seuil) | Seuil dans la règle, ou lookup index | |
| Low-level discovery (LLD) | Regroupement dynamique (`STATS … BY`) | Pas de découverte à déclarer : un nouvel objet est pris en compte automatiquement |
| Fonction `nodata()` | Règle « données manquantes » | |
| Recovery expression | État `recovered`, fenêtre et délai d'alerte de la règle | Pas de seuil de retour distinct (section 8) |
| Action, media type | Connecteur (email, webhook, ITSM, index) | |
| Escalade | Outil d'astreinte ou ITSM | Pas d'escalade multi-étapes native |
| Maintenance | Maintenance window Kibana | |
| Dépendance entre triggers | Pas d'équivalent natif | Section 8 |
| Web scenario | Elastic Synthetics (HTTP, navigateur) | |
| Check simple (ping, port) | Synthetics (ICMP, TCP) ou Osquery | |

---

## 5. Architecture cible

```mermaid
flowchart LR
  subgraph H[Hôtes supervisés]
    A[Elastic Agent<br/>métriques, logs, Osquery]
    S[Synthetics<br/>private location]
  end

  subgraph M[Cluster de gestion]
    F[Fleet Server]
    KM[Kibana Fleet<br/>agent policies, intégrations]
  end

  subgraph R[Cluster de supervision remote : données et alerting]
    ES[(Elasticsearch<br/>data streams des intégrations)]
    KR[Kibana<br/>règles d'alerte]
    J[(Journal des alertes<br/>alerts-&lt;env&gt;)]
    EL[(.kibana-event-log-*)]
  end

  A <-->|politiques, état| F
  KM --- F
  KM -.->|synchronisation des intégrations<br/>templates, ingest pipelines| ES
  A -->|output remote des intégrations| ES
  S -->|résultats| ES
  ES -->|"requêtes ES#124;QL"| KR
  KR --> C[Connecteurs]
  C --> N[Notifications<br/>email, ITSM, astreinte]
  C -->|connecteur Index| J
  KR -.-> EL
  J -->|datasource lecture seule| G[Grafana<br/>dashboard des alertes]
  EL -.->|santé des règles| G
```

**Deux clusters, deux rôles.** Le cluster de gestion porte Fleet : il pilote les agents (policies, intégrations, mises à jour) mais ne reçoit pas leurs données. Les intégrations envoient leurs données vers une **sortie (output) de type remote Elasticsearch**, qui pointe vers le cluster de supervision. C'est sur ce cluster que sont stockées les données, créées les règles Kibana, écrit le journal des alertes et branché Grafana.

- **Assets des intégrations** : index templates et ingest pipelines doivent exister sur le cluster remote. Sinon, les données arrivent avec un mapping dynamique et des messages non découpés, et les règles ou Stack Monitoring ne les lisent pas correctement. On utilise la synchronisation des intégrations de la sortie remote, ou une installation des assets dans la Kibana du remote, dans la même version que la policy Fleet.
- **Choix de la sortie** : réglé par intégration ou par agent policy dans Fleet. Toute intégration laissée sur la sortie par défaut écrit sur le cluster de gestion, et ses données échappent aux règles.
- **Self-monitoring des agents** : [sortie remote / sortie par défaut, à préciser], selon l'endroit où l'on veut suivre l'état des agents.

**Composants**

| Composant | Rôle | Détail |
|---|---|---|
| Elastic Agent | Collecte sur chaque hôte | [N] agents, version [ ] |
| Fleet (cluster de gestion) | Gestion centralisée des agents | Agent policies : [liste] |
| Intégrations | Définissent ce qui est collecté | [System, Windows, Linux, Osquery Manager, Logstash…] |
| Sortie remote Elasticsearch | Envoie les données des intégrations vers le cluster de supervision | Nom de la sortie : [ ] ; clé d'API en écriture `metrics-*`, `logs-*` ; synchronisation des intégrations : [oui / non] |
| Cluster de supervision (remote) | Stockage des données, règles Kibana, journal des alertes | [nom, version] |
| Synthetics | Tests depuis l'extérieur (ping, port, HTTP) | Private locations : [ ] |
| Règles Kibana | Détection | [N] règles |
| Connecteurs | Notifications | [email, webhook, ITSM…] |
| Journal des alertes | Historique des déclenchements et levées, un document par événement | Index `alerts-<env>`, rétention [ ] (ILM) |
| Grafana | Tableau de bord des alertes pour l'exploitation | Lit le journal des alertes ; aucune règle d'alerte dans Grafana |

### 5.1 Tableau de bord des alertes dans Grafana

**Kibana détecte, Grafana affiche.** Toutes les règles sont dans Kibana. Chaque règle écrit ses déclenchements et ses levées dans un index Elasticsearch dédié, le journal des alertes, que Grafana lit en lecture seule. Grafana ne porte aucune règle d'alerte : il n'y a donc qu'un seul endroit où les seuils sont définis.

**Écriture du journal**

- Chaque règle a deux actions qui passent par le même connecteur de type **Index** : une au déclenchement (`firing`), une à la levée (`recovered`).
- Le contenu de chaque document est défini par un gabarit de message propre à la règle. Les gabarits sont versionnés avec les règles.
- L'index a un **mapping strict** (modèle d'index) et une politique ILM pour la rétention.
- Un **ingest pipeline** attaché à l'index complète les documents de levée. À la levée, Kibana ne fournit que l'identifiant de l'alerte (`alert_id`, valeurs du regroupement de la règle). Le pipeline en déduit l'hôte et le pipeline concernés grâce au champ `alert_key_fields`, qui donne le nom de ces colonnes.

**Champs du journal**

| Champ | Contenu |
|---|---|
| `time` | Heure de l'événement (champ de temps de la datasource Grafana) |
| `rule`, `rule_id` | Nom et identifiant de la règle |
| `alert_id` | Identifiant de l'alerte dans la règle (ex. `srv-01,tcp/5514`) : relie une levée à son déclenchement |
| `alert_uuid` | Identifiant unique d'une occurrence d'alerte : sert au calcul des durées |
| `host`, `pipeline` | Hôte et objet concernés |
| `priority`, `severity` | `P1` / `critical`, `P2` / `warning` ; `ok` à la levée |
| `status` | `firing` ou `recovered` |
| `problem` | Message lisible, propre à la règle |
| `alert_key_fields` | Noms des colonnes qui composent `alert_id` |

**Datasources Grafana**

| Datasource | Index | Usage |
|---|---|---|
| Journal des alertes | `alerts-<env>`, champ de temps `time` | Tous les panneaux du dashboard |
| Event log Kibana | `.kibana-event-log-*` | Santé des règles : exécutions en échec, durée d'exécution |

Les datasources utilisent un compte technique en **lecture seule**, limité à ces index.

**Panneaux du dashboard « Alertes »**

| Panneau | Type | Contenu |
|---|---|---|
| Total alerts | Tuile | Nombre d'alertes sur la période |
| Critical / Warning | Tuiles | Alertes ouvertes par sévérité |
| Nodes in alert | Tuile | Nombre d'hôtes ayant au moins une alerte ouverte |
| Oldest open alert | Tuile | Âge de l'alerte ouverte la plus ancienne |
| Active alerts | Tableau | Alertes ouvertes : priorité, règle, hôte, objet, message, depuis |
| Alert history | Tableau | Déclenchements et levées avec la durée de chaque alerte |
| New alerts per rule | Courbe | Nouvelles alertes par règle dans le temps, pour repérer les règles bruyantes |
| Alert rule health | Tableau | Règles en échec ou lentes, depuis l'event log Kibana |

**Calcul des alertes ouvertes** : pour chaque couple `rule_id` + `alert_id`, Grafana garde le dernier document par `time`. L'alerte est ouverte si ce document a `status = firing`. Ce calcul s'appuie sur un historique de [30] jours, quelle que soit la période affichée.

**Points d'attention**

- Une alerte reste affichée comme ouverte si son document de levée n'est pas écrit ou ne porte pas exactement les mêmes `rule_id` et `alert_id`. C'est le premier point à vérifier en cas d'écart entre Kibana et Grafana.
- Les requêtes Grafana renvoient un nombre limité de documents (taille de la requête). Le dimensionner selon le volume du journal, sinon le dernier événement d'une alerte peut manquer.
- Chaque règle doit se terminer par un regroupement explicite (`STATS … BY`) sur la clé de l'alerte, pour que `alert_id` soit stable et unique.

**Conventions**

- **Nommage des règles** : `[Priorité] Composant – symptôme (source)`, par exemple `[P1] Logstash – process not running (Osquery)`.
- **Tags** : environnement, priorité, composant, source.
- **Règles gérées comme du code** : chaque règle est un fichier JSON versionné et déployé par script. Aucune modification manuelle dans l'interface.

---

## 6. Méthode de migration

**Démarche**

1. Import de l'Excel Zabbix comme référentiel unique.
2. Analyse de chaque trigger : ce qu'il détecte réellement, sa fréquence de déclenchement, son utilité.
3. Classement dans l'un des 5 statuts ci-dessous.
4. Écriture de la règle Kibana, tests de déclenchement et de levée.
5. Double run avec Zabbix (section 10).

**Statuts**

| Statut | Définition |
|---|---|
| Migrée à l'identique | Même donnée, même seuil, même comportement |
| Migrée avec adaptation | Même objectif, mécanisme différent (fenêtre, agrégation, source) |
| Remplacée | Autre approche plus adaptée, par exemple process via Osquery, port via Synthetics |
| Non migrable | Aucun équivalent avec Elastic Agent et Kibana, ou équivalent trop coûteux |
| Abandonnée | Obsolète, doublon ou jamais déclenchée ; justification obligatoire |

**Correspondance des sévérités**

| Zabbix | Elastic | Notification |
|---|---|---|
| Disaster | P1 | [astreinte immédiate] |
| High | P1 | [ ] |
| Average | P2 | [ ] |
| Warning | P3 | [ ] |
| Information | Pas d'alerte, dashboard uniquement | — |

*À ajuster selon les règles d'astreinte en vigueur.*

**Règles de conversion**

- **Fréquence** : les règles s'exécutent toutes les [1] minute(s) ; la fenêtre d'analyse couvre au moins 2 collectes.
- **Délai d'alerte** : une alerte n'est levée qu'après [N] exécutions consécutives en défaut, l'équivalent des fonctions `min()` / `avg()` sur une durée dans Zabbix.
- **Une règle par symptôme**, appliquée à tous les hôtes concernés, au lieu d'un trigger par hôte et par template.

---

## 7. Matrice de correspondance

*Résumé par famille. La matrice complète, ligne par ligne, est l'Excel d'origine enrichi (annexe A).*

| Famille | Alertes Zabbix | Migrées | Adaptées / remplacées | Non migrables | Abandonnées |
|---|---|---|---|---|---|
| Système (CPU, RAM, disque, swap) | [ ] | [ ] | [ ] | [ ] | [ ] |
| Disponibilité (ping, agent, nodata) | [ ] | [ ] | [ ] | [ ] | [ ] |
| Process et services | [ ] | [ ] | [ ] | [ ] | [ ] |
| Ports et applicatif (HTTP, TCP) | [ ] | [ ] | [ ] | [ ] | [ ] |
| Logs | [ ] | [ ] | [ ] | [ ] | [ ] |
| Middleware / bases de données | [ ] | [ ] | [ ] | [ ] | [ ] |
| Réseau / équipements | [ ] | [ ] | [ ] | [ ] | [ ] |
| Scripts personnalisés | [ ] | [ ] | [ ] | [ ] | [ ] |
| **Total** | **[N]** | | | **[~10]** | |

**Colonnes ajoutées à l'Excel d'origine**

| Colonne | Contenu |
|---|---|
| Statut | Un des 5 statuts de la section 6 |
| Règle Kibana (id) | Identifiant du fichier de règle |
| Nom de la règle | Nom affiché dans Kibana |
| Source | Data stream ou index, champ utilisé |
| Seuil / fenêtre / délai | Ex. `> 90 %`, 5 min, 3 exécutions |
| Priorité | P1, P2, P3 |
| Test de déclenchement | Date, provoqué ou observé, résultat |
| Test de levée | Date, résultat |
| Remarque | Écart de comportement, justification d'abandon |

---

## 8. Écarts de comportement

*Alertes migrées dont le comportement diffère de Zabbix. Le lecteur doit savoir ce qui change pour l'astreinte.*

| Écart | Zabbix | Elastic | Conséquence | Alertes concernées |
|---|---|---|---|---|
| Fréquence de collecte | [60 s] | [10–60 s selon l'intégration] | Détection plus rapide, parfois plus de bruit, compensé par le délai d'alerte | [ ] |
| Seuil de retour (hystérésis) | Recovery expression distincte | Retour dès que la condition n'est plus vraie sur la fenêtre | Risque de bascule rapide autour du seuil ; compensé par fenêtre et délai | [ ] |
| Dépendances entre triggers | Un hôte injoignable masque ses autres alertes | Pas d'équivalent natif | Plusieurs alertes lors d'une panne d'hôte ; atténué par la conception des règles et les maintenance windows | [ ] |
| Escalade | Étapes successives dans l'action | Une notification par changement d'état | Escalade à porter par l'outil d'astreinte ou l'ITSM | [ ] |
| Granularité | Un trigger par hôte | Une règle pour tous les hôtes, une alerte par hôte | Plus simple à maintenir ; vigilance sur les incidents massifs | [ ] |
| [À COMPLÉTER] | | | | |

---

## 9. Alertes non migrables

**[~10] alertes Zabbix n'ont pas d'équivalent direct avec Elastic Agent et Kibana Alerting.** Pour chacune, une alternative est proposée et une décision est attendue.

**Synthèse**

| # | Alerte Zabbix | Ce qu'elle surveille | Cause | Risque si non couverte | Alternative proposée | Décision | Responsable / échéance |
|---|---|---|---|---|---|---|---|
| 1 | [ ] | [ ] | [ ] | [Élevé / Moyen / Faible] | [ ] | [ ] | [ ] |
| 2 | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 3 | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 4 | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 5 | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 6 | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 7 | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 8 | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 9 | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |
| 10 | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] | [ ] |

**Causes et alternatives types**

| Cause | Pourquoi ce n'est pas migrable | Alternatives possibles |
|---|---|---|
| Script personnalisé (`UserParameter`, `system.run`) | Elastic Agent n'exécute pas de script arbitraire | Requête Osquery ; exposer la valeur en HTTP et la lire avec l'intégration HTTP JSON ; réécrire le contrôle |
| Envoi par l'application (`zabbix_sender`, trapper) | L'application pousse ses valeurs vers Zabbix | Envoi direct vers Elasticsearch ou Logstash ; écriture dans un log collecté |
| Commande à distance, auto-remédiation | Kibana ne lance pas d'action corrective sur l'hôte | Webhook vers un outil d'automatisation ; procédure manuelle documentée |
| Fonction prédictive (`forecast`, `timeleft`) | Pas de calcul prédictif dans les règles | Seuil anticipé ; détection d'anomalies (Machine Learning, licence requise) |
| Protocole ou équipement sans intégration (IPMI, SNMP spécifique, propriétaire) | Aucune intégration Elastic disponible | Intégration SNMP générique ; collecte via Logstash ; outil constructeur |
| [À COMPLÉTER] | | |

**Options de décision pour chaque alerte**

1. **Alternative technique** : développement à planifier, charge estimée [ ].
2. **Maintien temporaire de Zabbix** sur ce sous-périmètre, avec une date de fin.
3. **Autre outil** déjà en place (supervision réseau, outil constructeur…).
4. **Acceptation formelle du risque**, signée par le responsable du service concerné.

**Fiche détaillée** *(une par alerte, si le tableau ne suffit pas)*

> **Alerte n° [ ] – [nom du trigger Zabbix]**
> - **Expression Zabbix** : `[ ]`
> - **Hôtes concernés** : [ ]
> - **Ce qu'elle détecte** : [ ]
> - **Déclenchements sur les 12 derniers mois** : [ ]
> - **Cause de non-migration** : [ ]
> - **Risque si non couverte** : [ ]
> - **Recommandation** : [ ]
> - **Décision / responsable / échéance** : [ ]

---

## 10. Recette et validation

**Tests unitaires de chaque règle**

| Test | Méthode | Critère de réussite |
|---|---|---|
| Déclenchement | Incident provoqué (arrêt de service, remplissage disque, port fermé…) ou observé | Alerte levée dans le délai attendu, message correct |
| Levée | Retour à la normale | État `recovered` et notification de levée |
| Notification | Réception sur chaque canal | Email / ITSM / astreinte reçus |
| Non-régression | Aucune alerte en fonctionnement normal pendant [N] jours | Zéro faux positif |

**Double run** : Zabbix et Kibana tournent en parallèle du [date] au [date] ([N] semaines).

| Mesure | Résultat |
|---|---|
| Alertes Zabbix sur la période | [ ] |
| Dont aussi levées par Kibana | [ ] |
| Levées par Zabbix seul (manques) | [ ], analysées en annexe |
| Levées par Kibana seul | [ ], dont vrais positifs [ ] |
| Faux positifs Kibana | [ ] |
| Ajustements de seuils faits | [ ] |

---

## 11. Plan de bascule

| Étape | Contenu | Date | Critère pour passer à la suite |
|---|---|---|---|
| 1. Double run | Zabbix et Kibana actifs, seules les notifications Zabbix vont à l'astreinte | [ ] | Résultats de la section 10 validés |
| 2. Bascule des notifications | Notifications Kibana vers l'astreinte, Zabbix en observation | [ ] | [N] jours sans manque |
| 3. Arrêt de Zabbix | Notifications Zabbix coupées, serveur en lecture seule | [ ] | Décision go / no-go |
| 4. Décommissionnement | Désinstallation des agents Zabbix, archivage de la configuration, arrêt du serveur | [ ] | — |

**Critères de go / no-go** : couverture ≥ [ ] %, aucune alerte P1 manquée pendant le double run, décisions prises pour toutes les alertes non migrables.

**Retour arrière** : jusqu'à l'étape 3, réactivation des notifications Zabbix en [durée]. La configuration Zabbix est exportée et archivée avant l'étape 4.

---

## 12. Exploitation après migration

- **Circuit des notifications** : [canaux, destinataires, horaires d'astreinte].
- **Runbooks** : une fiche par règle P1 : ce qu'on vérifie, ce qu'on fait, qui escalader.
- **Ajout ou modification d'une règle** : modification du fichier JSON, revue, déploiement par script. Les règles ne sont pas modifiées dans l'interface.
- **Propriétaires** : chaque règle a une équipe propriétaire ([À COMPLÉTER]).
- **Revue périodique** : tous les [3] mois, revue des alertes les plus bruyantes et des règles jamais déclenchées.
- **Supervision de la supervision** : alertes sur les agents silencieux, les données manquantes et les règles en échec.

---

## 13. Risques et points ouverts

| Risque | Probabilité | Impact | Mesure |
|---|---|---|---|
| Alerte non migrable non couverte | [ ] | [ ] | Décision formelle par alerte (section 9) |
| Agent Elastic arrêté sur un hôte, aucune donnée | Moyenne | Élevé | Règle « données manquantes » |
| Incident massif générant de nombreuses alertes | Moyenne | Moyen | Règle de synthèse par groupe d'hôtes ; regroupement dans l'outil d'astreinte |
| Seuils mal calibrés (faux positifs) | Moyenne | Moyen | Double run et ajustements |
| Indisponibilité de la plateforme Elastic | Faible | Élevé | Supervision externe de la stack ; [ ] |
| Écart entre Kibana et le dashboard Grafana (alerte affichée ouverte à tort) | Moyenne | Moyen | Vérifier l'écriture des levées dans le journal ; panneau de santé des règles ; taille des requêtes Grafana |
| Montée en compétence des équipes | [ ] | [ ] | Formation, runbooks |

**Points ouverts**

- [ ] [À COMPLÉTER]
- [ ] [À COMPLÉTER]

---

## Annexes

**A. Matrice complète** : fichier `[nom du fichier]-migration.xlsx` (Excel d'origine + colonnes de la section 7).

**B. Liste des règles Kibana** : nom, identifiant, requête, seuil, fenêtre, priorité, connecteurs. *À générer depuis les fichiers JSON des règles.*

**C. Glossaire**

| Terme | Définition |
|---|---|
| Elastic Agent | Agent unique installé sur les hôtes, collecte métriques, logs et requêtes Osquery |
| Fleet | Gestion centralisée des agents et de leurs politiques |
| Agent policy | Ensemble d'intégrations appliqué à un groupe d'hôtes |
| Intégration | Module de collecte pour une technologie (System, Windows, Nginx…) |
| Data stream | Flux de données dans Elasticsearch, par type et source |
| ES\|QL | Langage de requête utilisé par les règles |
| Règle Kibana | Requête exécutée périodiquement qui lève des alertes |
| Connecteur | Canal de notification d'une règle |
| Maintenance window | Période pendant laquelle les notifications sont suspendues |
| Osquery | Requêtes SQL sur l'état du système (process, ports, fichiers) |
| Synthetics | Tests de disponibilité depuis l'extérieur (ping, port, HTTP) |
| Double run | Fonctionnement en parallèle de l'ancien et du nouvel outil |
| Journal des alertes | Index Elasticsearch où les règles écrivent leurs déclenchements et levées |
| Connecteur Index | Connecteur Kibana qui écrit un document dans un index Elasticsearch |
| Ingest pipeline | Traitement appliqué par Elasticsearch à un document avant son indexation |
| Event log Kibana | Index `.kibana-event-log-*` où Kibana trace l'exécution des règles et des actions |

**D. Références**

- Versions : Elastic Stack [ ], Elastic Agent [ ], intégrations [ ].
- Documentation Elastic : Fleet et Elastic Agent, Kibana Alerting, ES|QL, Synthetics.

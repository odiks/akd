1. Introduction
Objectifs du PRA :

L'objectif principal du Plan de Reprise d'Activité (PRA) est de garantir la disponibilité et la continuité des services du système SIEM, constitué des composants Graylog, OpenSearch, Logstash, MongoDB, Kafka, ainsi que des agents Beats (Winlogbeat et Filebeat). Ce PRA définit les actions à entreprendre en cas d'incidents majeurs, afin de limiter les interruptions de service et d'assurer une reprise rapide de l’activité.

Périmètre couvert :

Le présent PRA couvre exclusivement le système SIEM, c'est-à-dire les outils utilisés pour la collecte, l'analyse, la gestion et le stockage des logs (Graylog, OpenSearch, Logstash, MongoDB, Kafka et les agents Beats). Les systèmes externes ou les applications non intégrées au SIEM ne font pas partie de ce PRA.

Contexte de l’infrastructure :

L’architecture du SIEM est déployée sur un environnement multi-site avec une séparation réseau stricte. Chaque composant clé est répliqué sur trois nœuds, avec une répartition entre un site principal et un site secondaire, pour assurer la redondance et la tolérance aux pannes. Un nœud est dédié au site secondaire pour chaque outil, afin de garantir une haute disponibilité des services.

2. Architecture technique du SIEM
Description des composants et de leur répartition :

Le système SIEM est composé des éléments suivants :

Graylog : Système de gestion des logs. Composé de trois nœuds :
Site principal : 2 nœuds
Site secondaire : 1 nœud
Rôle : Analyse et visualisation des logs collectés.
OpenSearch : Système de stockage et de recherche de données.
Site principal : 2 nœuds
Site secondaire : 1 nœud
Rôle : Indexation et recherche des logs dans les bases de données.
Logstash : Pipeline de traitement des logs.
Site principal : 2 nœuds
Site secondaire : 1 nœud
Rôle : Traitement et acheminement des logs vers Graylog et OpenSearch.
MongoDB : Base de données NoSQL utilisée par Graylog pour stocker les métadonnées.
Site principal : 2 nœuds
Site secondaire : 1 nœud
Rôle : Stockage des données de configuration et des métadonnées.
Kafka : Système de messagerie asynchrone pour le traitement des flux de données.
Site principal : 2 nœuds
Site secondaire : 1 nœud
Rôle : Gestion des files de messages entre les différents composants du SIEM.
Beats (Winlogbeat et Filebeat) : Agents installés sur les serveurs pour envoyer les logs aux composants SIEM.
Rôle : Collecte des logs des serveurs et transmission aux autres composants du SIEM.
Schéma logique de l'architecture :

(Un schéma simplifié peut être inclus pour illustrer les interactions entre les nœuds et les composants.)

3. Analyse des risques
Risques de perte d’un site :

Site principal : La perte du site principal entraînerait l’indisponibilité de la majorité des nœuds du SIEM, affectant la collecte, le traitement et l'analyse des logs.
Site secondaire : La perte du site secondaire impacterait la redondance, mais le SIEM devrait continuer à fonctionner avec les nœuds du site principal, sous réserve que le quorum des services de stockage et de messagerie soit maintenu.
Risques de perte de quorum :

MongoDB : Perte de quorum si moins de deux nœuds MongoDB sont fonctionnels. Cela entraînerait des problèmes d’accès aux métadonnées et à la configuration.
OpenSearch : Perte de quorum si moins de deux nœuds OpenSearch sont fonctionnels, ce qui affecterait l’indexation et la recherche des logs.
Kafka : Perte de quorum de Kafka affecterait la gestion des flux de données entre les composants, ralentissant ou bloquant le traitement des logs.
Risques liés aux agents Beats :

La défaillance des agents Beats sur les serveurs source des logs pourrait entraîner la perte de données, ou un retard dans la collecte des logs, affectant la visibilité en temps réel du système.
4. Procédures de reprise
Reprise en cas de perte d’un nœud isolé :

Identifier la panne : Vérifier l'état du nœud via les outils de supervision.
Redémarrage du nœud : En cas de panne logicielle, redémarrer le nœud affecté.
Vérification de l'intégrité : Une fois le nœud redémarré, vérifier les logs et la synchronisation avec les autres nœuds pour s'assurer que le service fonctionne correctement.
Reprise en cas de perte du site secondaire (nœud unique par outil) :

Activation des nœuds du site principal : Les nœuds du site principal doivent prendre en charge toutes les fonctions du SIEM.
Vérification de la réplication inter-site : Assurez-vous que les données critiques sont bien répliquées entre les nœuds du site principal.
Reconstruction du site secondaire : À court terme, ajouter un nœud supplémentaire sur le site secondaire pour rétablir la redondance.
Reprise en cas de perte du site principal :

Activation du site secondaire : Faire basculer le traitement et l'analyse des logs sur les nœuds du site secondaire.
Vérification de la connectivité : Assurez-vous que tous les services (Graylog, OpenSearch, MongoDB, Kafka) sont opérationnels sur le site secondaire.
Restauration de la configuration : Si nécessaire, restaurer les configurations critiques (MongoDB, Kafka, etc.) depuis les sauvegardes disponibles.
Ordre de redémarrage des composants :

MongoDB : Démarrer MongoDB en premier pour assurer la disponibilité des métadonnées.
Kafka : Démarrer Kafka pour rétablir la gestion des flux de messages.
OpenSearch : Démarrer OpenSearch pour la réindexation et la recherche des logs.
Logstash : Démarrer Logstash pour traiter les flux de logs entrants.
Graylog : Démarrer Graylog en dernier pour reprendre l’analyse et la visualisation des logs.
5. Pré-requis et sauvegardes
Sauvegardes :

Configuration des outils : Sauvegarde régulière des fichiers de configuration de Graylog, OpenSearch, Logstash, MongoDB et Kafka.
Index OpenSearch : Sauvegarde quotidienne des indices d'OpenSearch pour préserver les logs historiques.
MongoDB : Sauvegarde régulière des bases de données MongoDB, y compris les données de configuration de Graylog.
Kafka : Sauvegarde des configurations de Kafka et des topics essentiels.
Agents Beats : Sauvegarde des configurations des agents Beats installés.
Synchronisation inter-site :

MongoDB, OpenSearch et Kafka doivent être configurés pour répliquer les données entre les sites principal et secondaire de manière automatique et transparente.
6. Tests de PRA
Fréquence des tests :

Tests de PRA complets : Tous les six mois.
Tests de composants critiques (MongoDB, Kafka, OpenSearch) : Tous les trois mois.
Procédures de validation :

Simulation de défaillance de nœud : Tester le redémarrage des nœuds isolés.
Simulation de perte de site : Tester le basculement complet du site principal vers le site secondaire.
Vérification des logs : S’assurer que les logs sont correctement collectés et analysés pendant le test de reprise.
7. Responsabilités et contacts
Équipe en charge de l’exploitation :

Administrateurs système : Responsable de la gestion des nœuds et de la configuration des outils.
Équipe de support : En charge du diagnostic et de l’escalade des pannes.
Support externe : Contacter les fournisseurs de Graylog, OpenSearch, Kafka et MongoDB en cas de défaillance non résolue en interne.
Escalade :

En cas de panne non résolue dans un délai de 4 heures, contacter le support technique externe pour chaque composant.
8. RTO (Objectifs de Temps de Reprise) et RPO (Objectifs de Point de Reprise)
Composant	RTO (temps de reprise)	RPO (perte maximale de données)
Graylog	30 minutes	1 heure
OpenSearch	1 heure	2 heures
MongoDB	1 heure	30 minutes
Kafka	1 heure	1 heure
Logstash	30 minutes	1 heure
Agents Beats	1 heure	30 minutes

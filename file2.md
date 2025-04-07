🛡️ Plan de Continuité d’Activité (PCA) – Système SIEM

1. Objectif du PCA
Assurer la continuité du service SIEM (collecte, traitement, indexation et visualisation des logs) en cas d’incident partiel ou total affectant une partie de l’infrastructure (perte d’un site, d’un nœud, d’un composant), sans interruption ou avec une interruption minimale.

2. Périmètre du PCA
Composants SIEM couverts :
Graylog
OpenSearch
Logstash
MongoDB
Kafka
Agents Beats (Filebeat, Winlogbeat)
Exclusions : Services non liés au SIEM, applications métiers externes, équipements réseaux hors périmètre SIEM.
3. Architecture de continuité
Redondance par cluster 3 nœuds pour chaque composant :
2 nœuds sur site principal
1 nœud sur site secondaire
Objectif : Maintenir un quorum suffisant pour assurer la continuité sans intervention manuelle immédiate.
Communication inter-site sécurisée : VPN chiffré ou VLAN dédié.
4. Scénarios de continuité et mécanismes de bascule
✅ Perte d’un nœud isolé

Impact : Faible – les clusters continuent de fonctionner.
Action : Aucun basculement nécessaire. Surveillance + notification.
✅ Perte du site secondaire

Impact : Le cluster perd 1 nœud sur 3, mais le quorum est conservé.
Continuité assurée : Oui, les services restent actifs.
Action : Surveillance + plan de reconstruction du nœud distant.
✅ Perte du site principal

Impact :
Reste 1 nœud sur chaque cluster → quorum perdu → services en panne.
Prévoir un basculement manuel ou automatique sur le site secondaire.
Pré-requis :
Mise en place d’un mécanisme de bascule active/passive.
Configuration possible en mode split-brain aware ou election forced quorum pour certains composants.
5. Mécanismes de résilience technique
Composant	Mode de résilience	Particularités
MongoDB	ReplicaSet 3 nœuds	Bascule automatique si 2 nœuds vivants minimum
OpenSearch	Cluster 3 nœuds	Répartition des shards + réplication
Kafka	Cluster 3 brokers	Partitions avec réplicas min=2
Graylog	Cluster stateless	Haute dispo si MongoDB et OpenSearch vivants
Logstash	Load-balanced	Stateless, redémarrage rapide possible
Beats	Buffers locaux	Continue à envoyer dès que serveur SIEM dispo
6. Objectifs de disponibilité
Composant	RTO (objectif de reprise)	RPO (perte maximale de données)	Disponibilité cible
MongoDB	5 minutes	0 données perdues	99.95%
OpenSearch	10 minutes	≤ 15 min de logs (si non répliqué)	99.9%
Kafka	5 minutes	≤ 10 min (si buffers Beats saturés)	99.95%
Graylog	10 minutes	0 si MongoDB/OS vivants	99.9%
Logstash	5 minutes	0 (stateless)	99.95%
Beats	N/A (agent local)	15 min max (en buffer local)	99.9%
7. Actions préventives et automatisation
Supervision continue : Alertes Prometheus/Grafana ou autre outil équivalent.
Scripts de bascule manuelle : Préparés pour réinitialiser les rôles en cas de perte de quorum.
Sauvegardes régulières :
MongoDB : Snapshots ou mongodump
OpenSearch : Snapshots S3/local
Configs : Sauvegardes Ansible ou GitOps
Tests de bascule inter-site simulés tous les 6 mois.
8. Procédure en cas d'incident majeur
Détection automatique via supervision (perte de nœud ou service).
Bascule automatique (si implémentée) vers le site secondaire.
Communication vers les équipes (Slack, mail, ticketing).
Lancement de la procédure PRA si continuité impossible.
Post-mortem et retour d’expérience (REx).
9. Responsabilités et gouvernance
Rôle	Responsable
Supervision et détection	Équipe Exploitation SIEM
Validation de bascule	Responsable Infrastructure
Bascule technique manuelle	Administrateurs systèmes
Communication interne	Responsable SSI / Production
Support éditeur (si besoin)	Contrat de support tiers
10. Annexes utiles
Liste des nœuds et leur rôle (naming convention, IP)
Commandes de bascule rapide
Documentation technique Graylog/OpenSearch/Kafka HA
Procédures d'accès aux backups
Scripts de test de bascule

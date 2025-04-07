🛡️ Plan de Continuité d’Activité (PCA) – Système SIEM

1. Objectif du PCA

Assurer la continuité du service SIEM (collecte, traitement, indexation et visualisation des logs) en cas d’incident partiel ou total affectant une partie de l’infrastructure (perte d’un site, d’un nœud ou d’un composant), sans interruption ou avec interruption minimale.

2. Périmètre du PCA

Composants couverts :

Graylog
OpenSearch
Logstash
MongoDB
Kafka
Beats (Filebeat, Winlogbeat)
Exclusions :

Services non liés au SIEM (applications métiers, composants réseau non SIEM, etc.)
3. Architecture de continuité

Architecture en clusters 3 nœuds pour chaque composant :
2 nœuds sur le site principal
1 nœud sur le site secondaire
Objectif : Maintenir un quorum minimal pour assurer la disponibilité sans intervention manuelle.
Communication inter-site : sécurisée via VPN ou VLAN dédié.
4. Scénarios de continuité

Perte d’un nœud isolé
Impact : Faible
Continuité assurée : Oui
Action : Surveillance, aucune bascule nécessaire
Perte du site secondaire
Impact : Perte d’un nœud sur chaque cluster
Continuité assurée : Oui (quorum conservé)
Action : Surveillance + reconstruction du nœud distant
Perte du site principal
Impact : Quorum perdu (1 nœud restant)
Continuité assurée : Non, nécessite bascule manuelle/automatisée
Pré-requis : Mécanisme de bascule active/passive ou forçage de quorum
5. Mécanismes de résilience technique

Composant	Mode de résilience	Particularités
MongoDB	ReplicaSet 3 nœuds	Bascule automatique si quorum
OpenSearch	Cluster avec réplication	Répartition de shards
Kafka	Cluster 3 brokers	Réplicas de partitions
Graylog	Stateless + HA backend	Dépend de MongoDB et OpenSearch
Logstash	Load-balancing	Stateless, redémarrage rapide
Beats	Buffers locaux	Reprise automatique des envois
6. Objectifs de disponibilité

Composant	RTO (Objectif de reprise)	RPO (Perte de données admise)	Disponibilité cible
MongoDB	5 minutes	0 données perdues	99.95%
OpenSearch	10 minutes	≤ 15 min (si shard non répliqué)	99.9%
Kafka	5 minutes	≤ 10 min	99.95%
Graylog	10 minutes	0	99.9%
Logstash	5 minutes	0	99.95%
Beats	N/A (local)	15 minutes max (buffer)	99.9%
7. Actions préventives

Supervision active (Prometheus, Grafana, etc.)
Scripts de bascule manuelle prêts
Sauvegardes régulières :
MongoDB : mongodump ou snapshot
OpenSearch : snapshots S3 ou FS local
Configuration : sauvegarde Ansible ou GitOps
Tests semestriels de bascule
8. Procédure en cas d’incident

Détection via supervision
Bascule automatique ou manuelle
Communication vers les équipes (ticketing, Slack/mail)
Lancement du PRA si indisponibilité totale
Retour d’expérience post-incident
9. Responsabilités

Rôle	Responsable
Supervision	Équipe d’exploitation SIEM
Validation de bascule	Responsable infrastructure
Intervention technique	Admins systèmes
Communication	Responsable production
Support externe	Contrat éditeur/tiers
10. Annexes utiles (à joindre au document final)

Liste des nœuds (nom, IP, rôle)
Scripts de bascule
Documentation HA par composant
Procédure de restauration depuis backups
Guide de test de continuité

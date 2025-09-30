GitHub.

---

# Application de Transfert de Fichiers via Kafka

Cette application fournit une solution robuste, performante et de qualité production pour transférer des fichiers de manière sécurisée et fiable en utilisant Apache Kafka comme bus de messagerie.

## Fonctionnalités Principales

-   **Scalabilité Exceptionnelle** : Grâce à une approche de **streaming**, les fichiers sont traités *chunk par chunk* sans jamais être chargés entièrement en mémoire, permettant le transfert de fichiers de plusieurs téraoctets.
-   **Transfert Complet des Métadonnées** : Conserve les permissions (POSIX & ACLs Windows), les timestamps étendus et les propriétaires (configurable).
-   **Fiabilité Accrue** :
    -   **Vérification d'Intégrité** : Utilise des hashs (SHA-256/384/512) pour garantir qu'aucun bit n'est corrompu.
    -   **Gestion des Erreurs d'Envoi** : Un échec d'envoi d'un seul chunk entraîne l'annulation immédiate du transfert, garantissant la cohérence.
    -   **Validation "Fail-Fast"** : La configuration est validée au démarrage pour éviter les erreurs en cours d'exécution.
-   **Compression à la Volée** : Réduit l'utilisation de la bande passante avec `Gzip` ou `Snappy`.
-   **Chiffrement de Bout-en-Bout** : Sécurise les données en transit avec un chiffrement hybride RSA/AES (256 bits) robuste.
-   **Observabilité** : Fournit des logs de production clairs, incluant les plages d'offsets Kafka pour un audit précis de chaque transfert.
-   **Qualité Professionnelle** : Une suite complète de tests unitaires (JUnit 5 & Mockito) assure la stabilité et la non-régression du code.

## Prérequis

*   Java Development Kit (JDK) 11 ou supérieur
*   Apache Maven 3.6 ou supérieur
*   Un cluster Apache Kafka accessible
*   `openssl` (disponible sur Linux, macOS, et via Git Bash/WSL sur Windows)

## Compilation

Pour compiler le projet et créer un JAR exécutable unique (uber-jar), exécutez la commande suivante à la racine du projet :

```bash
mvn clean package
```
Le JAR se trouvera dans le répertoire `target/kafka-file-transfer-0.0.18.jar`.

## Configuration

L'application est pilotée par des fichiers de propriétés externes pour une flexibilité maximale.

1.  À côté du JAR généré, créez un répertoire `config/`.
2.  Éditez les fichiers `config/producer.properties` et `config/consumer.properties` en vous basant sur les modèles disponibles.
3.  Remplissez les informations de votre cluster Kafka et ajustez les options selon vos besoins.

### Génération des Clés (pour le Chiffrement)

Si vous activez le chiffrement (`encryption.enabled=true`), vous devez générer une paire de clés RSA pour le consommateur.

1.  À côté du JAR, créez un répertoire `security/`.
2.  Exécutez les commandes suivantes depuis la racine de votre projet :

    ```bash
    # 1. Générer la clé privée (ce fichier doit rester secret !)
    openssl genrsa -out security/consumer_private.key 2048

    # 2. Extraire la clé publique (ce fichier sera partagé avec le producteur)
    openssl rsa -in security/consumer_private.key -pubout -out security/consumer_public.pem
    ```
3.  Assurez-vous que les chemins vers ces fichiers sont correctement spécifiés dans vos fichiers de configuration (`encryption.consumer.public_key.path` et `encryption.private_key.path`).

## Exécution

### Lancer le Consommateur

Ouvrez un terminal et lancez le consommateur. Il se mettra en écoute des fichiers entrants.

```bash
java -jar target/kafka-file-transfer-0.0.18.jar consumer \
  --config config/consumer.properties \
  --destination-dir /chemin/vers/votre/repertoire/de/sortie
```

### Lancer le Producteur

Ouvrez un second terminal pour envoyer un fichier.

```bash
java -jar target/kafka-file-transfer-0.0.18.jar producer \
  --config config/producer.properties \
  /chemin/vers/votre/fichier/a/envoyer.zip
```

Une fois le transfert terminé, un message de succès s'affichera dans les logs du producteur, indiquant la plage d'offsets utilisée sur le topic Kafka.
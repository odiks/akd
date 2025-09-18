package com.example.kafka.filetransfer.model;

import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.serialization.ByteArrayDeserializer;
import org.apache.kafka.common.serialization.ByteArraySerializer;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;

import java.util.Properties;

/**
 * Un objet de configuration type-safe qui parse les propriétés depuis un fichier
 * et les expose via des getters. Centralise toute la logique de configuration
 * et prépare les objets Properties pour les clients Kafka.
 */
public class TransferConfig {

    private final Properties props;

    public TransferConfig(Properties props) {
        this.props = props;
        validateRequired();
    }

    /**
     * Valide que les propriétés minimales requises sont présentes.
     */
    private void validateRequired() {
        if (props.getProperty("bootstrap.servers") == null) {
            throw new IllegalArgumentException("La propriété 'bootstrap.servers' est requise.");
        }
        if (props.getProperty("topic.data") == null) {
            throw new IllegalArgumentException("La propriété 'topic.data' est requise.");
        }
    }

    // --- Getters de configuration générale ---
    public String getDataTopic() { return props.getProperty("topic.data"); }
    public String getStatusTopic() { return props.getProperty("topic.status", "files-status-topic"); }
    public int getChunkSize() { return Integer.parseInt(props.getProperty("chunk.size", "1048576")); }
    public String getHashAlgorithm() { return props.getProperty("hash.algorithm", "SHA-256"); }
    public String getCompressionAlgorithm() { return props.getProperty("compression.algorithm", "NONE"); }

    // --- Getters de configuration de sécurité ---
    public boolean isEncryptionEnabled() { return Boolean.parseBoolean(props.getProperty("encryption.enabled", "false")); }
    public String getConsumerPublicKeyPath() { return props.getProperty("encryption.consumer.public_key.path"); }
    public String getConsumerPrivateKeyPath() { return props.getProperty("encryption.private_key.path"); }

    // --- Getters de configuration de métadonnées (côté consumer) ---
    public boolean shouldRestorePermissions() { return Boolean.parseBoolean(props.getProperty("metadata.restore.permissions", "true")); }
    public boolean shouldRestoreOwner() { return Boolean.parseBoolean(props.getProperty("metadata.restore.owner", "false")); }
    public boolean shouldRestoreTimestamps() { return Boolean.parseBoolean(props.getProperty("metadata.restore.timestamps", "true")); }

    /**
     * Construit et retourne un objet Properties prêt à être utilisé par un KafkaProducer.
     * Cette méthode transmet toutes les propriétés du fichier de configuration,
     * permettant une configuration SSL ou avancée sans modifier le code.
     * @return Properties configurées pour le producteur.
     */
    public Properties getKafkaProducerProperties() {
        Properties kafkaProps = new Properties();
        // Copie toutes les propriétés du fichier (incluant security.protocol, ssl.*, etc.)
        kafkaProps.putAll(this.props);

        // S'assure que les sérialiseurs et autres valeurs par défaut sont bien définis
        kafkaProps.putIfAbsent(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, "true");
        kafkaProps.putIfAbsent(ProducerConfig.ACKS_CONFIG, "all");
        kafkaProps.putIfAbsent(ProducerConfig.RETRIES_CONFIG, "5");
        kafkaProps.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        kafkaProps.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, ByteArraySerializer.class.getName());

        return kafkaProps;
    }

    /**
     * Construit et retourne un objet Properties prêt à être utilisé par un KafkaConsumer.
     * Cette méthode transmet toutes les propriétés du fichier de configuration,
     * permettant une configuration SSL ou avancée sans modifier le code.
     * @return Properties configurées pour le consommateur.
     */
    public Properties getKafkaConsumerProperties() {
        Properties kafkaProps = new Properties();
        // Copie toutes les propriétés du fichier (incluant security.protocol, ssl.*, etc.)
        kafkaProps.putAll(this.props);

        // Valide la présence du group.id, crucial pour les consommateurs
        if (kafkaProps.getProperty(ConsumerConfig.GROUP_ID_CONFIG) == null) {
            throw new IllegalArgumentException("La propriété 'group.id' est requise pour le consommateur.");
        }

        // S'assure que les désérialiseurs et autres valeurs par défaut sont bien définis
        kafkaProps.putIfAbsent(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "false");
        kafkaProps.putIfAbsent(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        kafkaProps.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        kafkaProps.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, ByteArrayDeserializer.class.getName());

        return kafkaProps;
    }
}
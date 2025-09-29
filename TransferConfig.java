package com.example.kafka.filetransfer.model;

import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.serialization.ByteArrayDeserializer;
import org.apache.kafka.common.serialization.ByteArraySerializer;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;

import java.util.Optional;
import java.util.Properties;
import java.util.Set;

public class TransferConfig {

    private final Properties props;

    public enum AppMode {
        PRODUCER,
        CONSUMER
    }

    public enum SerializationFormat {
        PROTOBUF,
        JSON
    }

    public TransferConfig(Properties props, AppMode mode) {
        this.props = props;
        validate(mode);
    }

    private void validate(AppMode mode) {
        // --- Validations communes ---
        requireProperty("bootstrap.servers");
        requireProperty("topic.data");
       
        validatePropertyInSet("serialization.format", Set.of("PROTOBUF", "JSON"), "PROTOBUF");
        validatePropertyInSet("hash.algorithm", Set.of("SHA-256", "SHA-384", "SHA-512"), "SHA-256");
        
        // --- Validations spécifiques au Producteur ---
        if (mode == AppMode.PRODUCER) {
            validatePropertyInSet("compression.algorithm", Set.of("NONE", "GZIP", "SNAPPY"), "NONE");
            validatePositiveInteger("chunk.size", "1048576");
            
            // NOUVELLE VÉRIFICATION (boolean)
            validateBooleanProperty("encryption.enabled", "false");

            if (Boolean.parseBoolean(props.getProperty("encryption.enabled", "false"))) {
                requireProperty("encryption.consumer.public_key.path");
            }
        }
        
        // --- Validations spécifiques au Consommateur ---
        if (mode == AppMode.CONSUMER) {
            requireProperty(ConsumerConfig.GROUP_ID_CONFIG);
            requireProperty("staging.directory");
            //requireProperty("encryption.private_key.path");
            validatePositiveLong("transfer.timeout.hours", "24");

            // NOUVELLES VÉRIFICATIONS (boolean)
            validateBooleanProperty("metadata.restore.permissions", "true");
            validateBooleanProperty("metadata.restore.owner", "false");
            validateBooleanProperty("metadata.restore.timestamps", "true");
        }
    }
    
    // --- Getters ---
    public String getDataTopic() { return props.getProperty("topic.data"); }
    public String getStatusTopic() { return props.getProperty("topic.status"); } // NOUVEAU GETTER
    public int getChunkSize() { return Integer.parseInt(props.getProperty("chunk.size", "1048576")); }
    public String getHashAlgorithm() { return props.getProperty("hash.algorithm", "SHA-256"); }
    public String getCompressionAlgorithm() { return props.getProperty("compression.algorithm", "NONE"); }
    public String getStagingDirectory() { return props.getProperty("staging.directory", "/tmp/kafka-staging"); }
    public long getTransferTimeoutHours() { return Long.parseLong(props.getProperty("transfer.timeout.hours", "24")); }
    public SerializationFormat getSerializationFormat() { return SerializationFormat.valueOf(props.getProperty("serialization.format", "PROTOBUF").toUpperCase()); }
    public Optional<String> getRecoveryTransferId() {
        String recoveryId = props.getProperty("recovery.transfer.id");
        return (recoveryId != null && !recoveryId.trim().isEmpty()) ? Optional.of(recoveryId.trim()) : Optional.empty();
    }
    public boolean isEncryptionEnabled() { return Boolean.parseBoolean(props.getProperty("encryption.enabled", "false")); }
    public String getConsumerPublicKeyPath() { return props.getProperty("encryption.consumer.public_key.path"); }
    public String getConsumerPrivateKeyPath() { return props.getProperty("encryption.private_key.path"); }
    public boolean shouldRestorePermissions() { return Boolean.parseBoolean(props.getProperty("metadata.restore.permissions", "true")); }
    public boolean shouldRestoreOwner() { return Boolean.parseBoolean(props.getProperty("metadata.restore.owner", "false")); }
    public boolean shouldRestoreTimestamps() { return Boolean.parseBoolean(props.getProperty("metadata.restore.timestamps", "true")); }
    
    public Properties getKafkaProducerProperties() {
        Properties kafkaProps = new Properties();
        kafkaProps.putAll(this.props);
        kafkaProps.putIfAbsent(ProducerConfig.ENABLE_IDEMPOTENCE_CONFIG, "true");
        kafkaProps.putIfAbsent(ProducerConfig.ACKS_CONFIG, "all");
        kafkaProps.putIfAbsent(ProducerConfig.RETRIES_CONFIG, "5");
        kafkaProps.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class.getName());
        kafkaProps.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, ByteArraySerializer.class.getName());
        return kafkaProps;
    }

    public Properties getKafkaConsumerProperties() {
        Properties kafkaProps = new Properties();
        kafkaProps.putAll(this.props);
        kafkaProps.putIfAbsent(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "false");
        kafkaProps.putIfAbsent(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        kafkaProps.put(ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class.getName());
        kafkaProps.put(ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, ByteArrayDeserializer.class.getName());
        return kafkaProps;
    }
    
    private void requireProperty(String key) {
        if (props.getProperty(key) == null || props.getProperty(key).trim().isEmpty()) {
            throw new IllegalArgumentException("La propriété requise '" + key + "' est manquante ou vide.");
        }
    }
    
    private void validatePropertyInSet(String key, Set<String> allowedValues, String defaultValue) {
        String value = props.getProperty(key, defaultValue).toUpperCase();
        if (!allowedValues.contains(value)) {
            throw new IllegalArgumentException("Valeur invalide pour '" + key + "': '" + props.getProperty(key) + "'. Les valeurs autorisées sont : " + allowedValues);
        }
    }

    private void validatePositiveInteger(String key, String defaultValue) {
        try {
            int value = Integer.parseInt(props.getProperty(key, defaultValue));
            if (value <= 0) {
                throw new IllegalArgumentException("La propriété '" + key + "' doit être un entier positif, mais la valeur est : " + value);
            }
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("La propriété '" + key + "' doit être un entier valide, mais la valeur est : '" + props.getProperty(key) + "'");
        }
    }

    private void validatePositiveLong(String key, String defaultValue) {
        try {
            long value = Long.parseLong(props.getProperty(key, defaultValue));
            if (value <= 0) {
                throw new IllegalArgumentException("La propriété '" + key + "' doit être un nombre positif, mais la valeur est : " + value);
            }
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("La propriété '" + key + "' doit être un nombre valide, mais la valeur est : '" + props.getProperty(key) + "'");
        }
    }

    /**
     * NOUVELLE MÉTHODE DE VALIDATION
     * Vérifie qu'une propriété a bien la valeur "true" ou "false", de manière insensible à la casse.
     */
    private void validateBooleanProperty(String key, String defaultValue) {
        String value = props.getProperty(key, defaultValue);
        if (value == null) {
            return; // Ne devrait pas arriver avec une valeur par défaut
        }
        String trimmedValue = value.trim().toLowerCase();
        if (!"true".equals(trimmedValue) && !"false".equals(trimmedValue)) {
            throw new IllegalArgumentException("La propriété '" + key + "' doit avoir la valeur 'true' ou 'false', mais la valeur est : '" + props.getProperty(key) + "'");
        }
    }
}

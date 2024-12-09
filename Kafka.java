package com.example.kafka;

import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.streams.KafkaStreams;
import org.apache.kafka.streams.StreamsBuilder;
import org.apache.kafka.streams.StreamsConfig;
import org.apache.kafka.streams.kstream.KStream;

import java.io.FileInputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;

public class KafkaStreamsApp {
    public static void main(String[] args) throws IOException {
        // Charger les configurations à partir du fichier application.properties
        Properties appConfig = new Properties();
        try (FileInputStream fis = new FileInputStream("src/main/resources/application.properties")) {
            appConfig.load(fis);
        }

        // Configuration Kafka Streams
        Properties props = new Properties();
        props.put(StreamsConfig.APPLICATION_ID_CONFIG, appConfig.getProperty("application.id"));
        props.put(StreamsConfig.BOOTSTRAP_SERVERS_CONFIG, appConfig.getProperty("bootstrap.servers"));
        props.put(StreamsConfig.DEFAULT_KEY_SERDE_CLASS_CONFIG, Serdes.String().getClass());
        props.put(StreamsConfig.DEFAULT_VALUE_SERDE_CLASS_CONFIG, Serdes.String().getClass());

        // SSL Configuration
        props.put("security.protocol", appConfig.getProperty("security.protocol"));
        props.put("ssl.truststore.location", appConfig.getProperty("ssl.truststore.location"));
        props.put("ssl.truststore.password", appConfig.getProperty("ssl.truststore.password"));
        props.put("ssl.keystore.location", appConfig.getProperty("ssl.keystore.location"));
        props.put("ssl.keystore.password", appConfig.getProperty("ssl.keystore.password"));
        props.put("ssl.key.password", appConfig.getProperty("ssl.key.password"));

        // Construire le flux
        StreamsBuilder builder = new StreamsBuilder();

        // Lire les topics sources depuis les propriétés
        List<String> sourceTopics = new ArrayList<>();
        List<String> targetTopics = new ArrayList<>();

        for (String key : appConfig.stringPropertyNames()) {
            if (key.startsWith("source.topic.")) {
                sourceTopics.add(appConfig.getProperty(key));
            } else if (key.startsWith("target.topic.")) {
                targetTopics.add(appConfig.getProperty(key));
            }
        }

        // Créer des flux pour chaque topic source et les envoyer vers les topics de destination
        for (String sourceTopic : sourceTopics) {
            KStream<String, String> sourceStream = builder.stream(sourceTopic);
            for (String targetTopic : targetTopics) {
                sourceStream.to(targetTopic);
            }
        }

        // Lancer l'application Kafka Streams
        KafkaStreams streams = new KafkaStreams(builder.build(), props);
        streams.start();

        // Ajouter un hook pour arrêter l'application proprement
        Runtime.getRuntime().addShutdownHook(new Thread(streams::close));
    }
}

package com.example.kafka.filetransfer.consumer;

import com.example.kafka.filetransfer.model.FileChunkDto;
import com.example.kafka.filetransfer.model.InProgressTransfer;
import com.example.kafka.filetransfer.model.TransferConfig;
import com.example.kafka.filetransfer.proto.CompressionAlgorithm;
import com.example.kafka.filetransfer.proto.FileChunkMessage;
import com.example.kafka.filetransfer.service.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.protobuf.ByteString;
import com.google.protobuf.util.JsonFormat;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.errors.InterruptException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.file.*;
import java.security.GeneralSecurityException;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.stream.Collectors;
import java.util.stream.Stream;

public class FileTransferConsumer {

    private static final Logger logger = LoggerFactory.getLogger(FileTransferConsumer.class);

    private final TransferConfig config;
    private final Path destinationDir;
    private final KafkaConsumer<String, byte[]> kafkaConsumer;
    private final Map<String, InProgressTransfer> inProgressTransfers = new ConcurrentHashMap<>();
    private final AtomicBoolean running = new AtomicBoolean(true);
    private final Path stagingBaseDir;
    private final ScheduledExecutorService cleanupScheduler;
    private final CryptoService cryptoService;
    private final CompressionService compressionService;
    private final HashingService hashingService;
    private final ManifestService manifestService;
    private final ObjectMapper jsonMapper;

    public FileTransferConsumer(TransferConfig config, Path destinationDir) {
        this.config = config;
        this.destinationDir = destinationDir;
        this.kafkaConsumer = new KafkaConsumer<>(config.getKafkaConsumerProperties());
        this.stagingBaseDir = Paths.get(config.getStagingDirectory());
        try {
            Files.createDirectories(stagingBaseDir);
        } catch (IOException e) {
            throw new RuntimeException("Impossible d'initialiser le répertoire de transit.", e);
        }
        this.cleanupScheduler = Executors.newSingleThreadScheduledExecutor();
        this.cryptoService = new CryptoService(config);
        this.compressionService = new CompressionService();
        this.hashingService = new HashingService();
        this.manifestService = new ManifestService(config);
        this.jsonMapper = new ObjectMapper();
    }

    /**
     * Démarre le consommateur en mode d'écoute continue.
     */
    public void start() {
        long timeoutMillis = TimeUnit.HOURS.toMillis(config.getTransferTimeoutHours());
        cleanupScheduler.scheduleAtFixedRate(() -> cleanupAbandonedTransfers(timeoutMillis), 1, 1, TimeUnit.HOURS);
        kafkaConsumer.subscribe(Collections.singletonList(config.getDataTopic()));
        logger.info("Consommateur démarré. En écoute sur le topic '{}' au format '{}'", config.getDataTopic(), config.getSerializationFormat());
        logger.info("Nettoyage des transferts abandonnés activé (timeout: {} heures).", config.getTransferTimeoutHours());
        try {
            while (running.get()) {
                ConsumerRecords<String, byte[]> records = kafkaConsumer.poll(Duration.ofMillis(500));
                for (ConsumerRecord<String, byte[]> record : records) {
                    processRecord(record);
                }
                if (!records.isEmpty()) {
                    kafkaConsumer.commitSync();
                }
            }
        } catch (InterruptException e) {
            logger.info("Consommateur réveillé pour fermeture.");
        } finally {
            close();
        }
    }

    /**
     * Démarre le consommateur en mode de récupération ponctuelle pour un transfert spécifique.
     * @param recoveryTransferId L'ID complet du transfert à récupérer.
     * @return true si la récupération a réussi, false sinon.
     */
    public boolean startRecovery(String recoveryTransferId) {
        String tidLogPrefix = "[TID:" + recoveryTransferId.substring(0, 8) + "] ";
        logger.info("{}Recherche du transfert à récupérer dans le topic '{}'...", tidLogPrefix, config.getDataTopic());

        InProgressTransfer recoveryTransfer = null;
        try {
            List<TopicPartition> partitions = kafkaConsumer.partitionsFor(config.getDataTopic())
                    .stream()
                    .map(p -> new TopicPartition(p.topic(), p.partition()))
                    .collect(Collectors.toList());
            kafkaConsumer.assign(partitions);
            kafkaConsumer.seekToBeginning(partitions);
            logger.info("{}Lecture du topic depuis le début...", tidLogPrefix);

            final int maxEmptyPolls = 5;
            int emptyPollsCount = 0;

            while (true) {
                ConsumerRecords<String, byte[]> records = kafkaConsumer.poll(Duration.ofSeconds(2));
                if (records.isEmpty()) {
                    emptyPollsCount++;
                    if (emptyPollsCount >= maxEmptyPolls) {
                        logger.warn("{}Aucun autre message trouvé après plusieurs tentatives. Fin de la recherche.", tidLogPrefix);
                        break;
                    }
                    continue;
                }
                emptyPollsCount = 0;

                for (ConsumerRecord<String, byte[]> record : records) {
                    if (recoveryTransferId.equals(record.key())) {
                        if (recoveryTransfer == null) {
                            recoveryTransfer = new InProgressTransfer(stagingBaseDir.resolve(recoveryTransferId));
                            logger.info("{}Transfert trouvé ! Début de la collecte des chunks.", tidLogPrefix);
                        }

                        FileChunkMessage message = deserialize(record.value());
                        if (!message.getIsFinalChunk()) {
                            Path chunkPath = recoveryTransfer.getStagingDirectory().resolve(message.getChunkNumber() + ".chunk");
                            Files.write(chunkPath, message.getData().toByteArray());
                        }
                        recoveryTransfer.addChunkMetadata(message);
                        logger.debug("{}Chunk {}/{} collecté.", tidLogPrefix, message.getChunkNumber(), message.getTotalChunks());
                    }
                }

                if (recoveryTransfer != null && recoveryTransfer.isComplete()) {
                    logger.info("{}Tous les chunks du transfert ont été collectés.", tidLogPrefix);
                    break;
                }
            }

            if (recoveryTransfer != null && recoveryTransfer.isComplete()) {
                reconstructFile(recoveryTransfer, tidLogPrefix);
                return true;
            } else {
                logger.error("{}Impossible de trouver tous les chunks pour le transfert '{}'. La récupération a échoué. Les données ont peut-être expiré (rétention Kafka).", tidLogPrefix, recoveryTransferId);
                if (recoveryTransfer != null) {
                    cleanupTransferResources(null, recoveryTransfer.getStagingDirectory(), tidLogPrefix);
                }
                return false;
            }

        } catch (Exception e) {
            logger.error("{}Une erreur critique est survenue pendant la récupération : {}", tidLogPrefix, e.getMessage(), e);
            return false;
        } finally {
            close();
        }
    }

    private void processRecord(ConsumerRecord<String, byte[]> record) {
        FileChunkMessage message;
        try {
            message = deserialize(record.value());
            String transferId = message.getTransferId();
            String tidLogPrefix = "[TID:" + transferId.substring(0, 8) + "] ";
            InProgressTransfer transfer = inProgressTransfers.computeIfAbsent(transferId, k -> {
                try {
                    logger.info("{}Nouveau transfert détecté pour le fichier '{}'. Création du répertoire de transit.", tidLogPrefix, message.getFileName());
                    return new InProgressTransfer(stagingBaseDir.resolve(k));
                } catch (IOException e) {
                    throw new RuntimeException("Impossible de créer le répertoire de transit pour " + k, e);
                }
            });
            if (!message.getIsFinalChunk()) {
                Path chunkPath = transfer.getStagingDirectory().resolve(message.getChunkNumber() + ".chunk");
                Files.write(chunkPath, message.getData().toByteArray());
                logger.debug("{}Chunk {}/{} écrit sur le disque.", tidLogPrefix, message.getChunkNumber(), message.getTotalChunks());
            }
            transfer.addChunkMetadata(message);
            if (transfer.isComplete()) {
                logger.info("{}Tous les chunks pour le fichier '{}' ont été reçus. Démarrage de la reconstruction.", tidLogPrefix, message.getFileName());
                reconstructFile(transfer, tidLogPrefix);
                inProgressTransfers.remove(transferId);
            }
        } catch (IOException e) {
            logger.error("Impossible de désérialiser le message. Format invalide ? Offset: {}, Partition: {}. Message ignoré.", record.offset(), record.partition(), e);
        } catch (RuntimeException e) {
            logger.error("Erreur non gérée durant le traitement d'un message.", e);
        }
    }

    private FileChunkMessage deserialize(byte[] payload) throws IOException {
        switch (config.getSerializationFormat()) {
            case JSON:
                FileChunkDto dto = jsonMapper.readValue(payload, FileChunkDto.class);
                return mapDtoToProto(dto);
            case PROTOBUF:
            default:
                return FileChunkMessage.parseFrom(payload);
        }
    }

    private FileChunkMessage mapDtoToProto(FileChunkDto dto) throws IOException {
        String jsonString = jsonMapper.writeValueAsString(dto);
        FileChunkMessage.Builder builder = FileChunkMessage.newBuilder();
        JsonFormat.parser().ignoringUnknownFields().merge(jsonString, builder);
        if (dto.data != null && !dto.data.isEmpty()) {
            builder.setData(ByteString.copyFrom(Base64.getDecoder().decode(dto.data)));
        }
        if (dto.encryptedSymmetricKey != null && !dto.encryptedSymmetricKey.isEmpty()) {
            builder.setEncryptedSymmetricKey(ByteString.copyFrom(Base64.getDecoder().decode(dto.encryptedSymmetricKey)));
        }
        return builder.build();
    }

    private void reconstructFile(InProgressTransfer transfer, String tidLogPrefix) {
        FileChunkMessage metadata = transfer.getFinalChunkMetadata();
        String fileName = metadata.getFileName();
        Path finalPath = destinationDir.resolve(fileName);
        Path tempPath = Paths.get(finalPath + "." + metadata.getTransferId() + ".tmp");
        Path tempAssembledPath = Paths.get(finalPath + "." + metadata.getTransferId() + ".assembled");
        Path stagingDir = transfer.getStagingDirectory();

        try {
            logger.info("{}Événement de transfert : [STATUS={}] - Reconstruction démarrée pour '{}'", tidLogPrefix, "RECONSTRUCTION_STARTED", fileName);
            List<Path> sortedChunkFiles = transfer.getSortedChunkFiles();

            // Étape 1 : Assembler les chunks (après déchiffrement) dans le fichier temporaire "assembled".
            logger.info("{}Assemblage des chunks déchiffrés...", tidLogPrefix);
            byte[] encryptedSymmetricKey = metadata.getEncryptedSymmetricKey().toByteArray();
            String cipherName = metadata.hasEncryptionCipher() ? metadata.getEncryptionCipher().getValue() : null;
            boolean isEncrypted = !metadata.getEncryptedSymmetricKey().isEmpty() && cipherName != null;

            try (OutputStream os = Files.newOutputStream(tempAssembledPath)) {
                for (Path chunkFile : sortedChunkFiles) {
                    byte[] data = Files.readAllBytes(chunkFile);
                    if (isEncrypted) {
                        data = cryptoService.decrypt(data, encryptedSymmetricKey, cipherName);
                    }
                    os.write(data);
                }
            }

            // Étape 2 : Gérer la décompression (si nécessaire) pour créer le fichier final temporaire "tmp".
            if (metadata.getCompressionAlgorithm() != CompressionAlgorithm.NONE) {
                if (metadata.hasCompressedFileHash()) {
                    logger.info("{}Vérification de l'intégrité du fichier compressé...", tidLogPrefix);
                    hashingService.verifyFileIntegrity(tempAssembledPath, metadata.getCompressedFileHash().getValue(), metadata.getHashAlgorithm());
                    logger.info("{}Intégrité du fichier compressé vérifiée avec succès.", tidLogPrefix);
                } else {
                    logger.warn("{}Aucun hash de fichier compressé n'a été fourni pour la vérification.", tidLogPrefix);
                }

                logger.info("{}Décompression du fichier...", tidLogPrefix);
                byte[] compressedBytes = Files.readAllBytes(tempAssembledPath);
                byte[] decompressedBytes = compressionService.decompress(compressedBytes, metadata.getCompressionAlgorithm().name());
                Files.write(tempPath, decompressedBytes);
            } else {
                Files.move(tempAssembledPath, tempPath, StandardCopyOption.REPLACE_EXISTING);
            }

            // Étape 3 : Vérification de l'intégrité finale sur le fichier décompressé.
            logger.info("{}Vérification finale de l'intégrité sur le fichier décompressé...", tidLogPrefix);
            hashingService.verifyFileIntegrity(tempPath, metadata.getFileHash(), metadata.getHashAlgorithm());
            logger.info("{}Intégrité du fichier original ('{}') vérifiée avec succès.", tidLogPrefix, fileName);

            // Étape 4 : Appliquer les métadonnées et déplacer vers la destination finale.
            manifestService.applyMetadata(tempPath, metadata);
            logger.info("{}Métadonnées appliquées au fichier '{}'.", tidLogPrefix, fileName);
            Files.move(tempPath, finalPath, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            logger.info("{}Événement de transfert : [STATUS={}] - Fichier '{}' reconstruit avec succès à {}", tidLogPrefix, "RECONSTRUCTION_SUCCESS", fileName, finalPath);

        } catch (IOException | GeneralSecurityException | SecurityException e) {
            logger.error("{}Événement de transfert : [STATUS={}] - Échec de la reconstruction pour le fichier '{}' : {}", tidLogPrefix, "TRANSFER_FAILED", fileName, e.getMessage(), e);
        } finally {
            cleanupTransferResources(tempPath, stagingDir, tidLogPrefix);
            cleanupTransferResources(tempAssembledPath, null, tidLogPrefix);
        }
    }

    private void cleanupAbandonedTransfers(long timeoutMillis) {
        logger.info("Exécution de la tâche de nettoyage des transferts abandonnés...");
        long currentTime = System.currentTimeMillis();
        int cleanupCount = 0;
        Iterator<Map.Entry<String, InProgressTransfer>> iterator = inProgressTransfers.entrySet().iterator();
        while (iterator.hasNext()) {
            Map.Entry<String, InProgressTransfer> entry = iterator.next();
            InProgressTransfer transfer = entry.getValue();
            if (currentTime - transfer.getLastUpdateTime() > timeoutMillis) {
                String transferId = entry.getKey();
                String tidLogPrefix = "[TID:" + transferId.substring(0, 8) + "] ";
                logger.warn("{}Transfert abandonné détecté (dépassement du timeout de {} ms). Nettoyage des ressources.", tidLogPrefix, timeoutMillis);
                cleanupTransferResources(null, transfer.getStagingDirectory(), tidLogPrefix);
                iterator.remove();
                cleanupCount++;
            }
        }
        if (cleanupCount > 0) {
            logger.info("Nettoyage terminé. {} transfert(s) abandonné(s) supprimé(s).", cleanupCount);
        }
    }

    private void cleanupTransferResources(Path tempFile, Path stagingDir, String tidLogPrefix) {
        try {
            if (tempFile != null && Files.exists(tempFile)) {
                Files.delete(tempFile);
                logger.debug("{}Fichier temporaire '{}' nettoyé.", tidLogPrefix, tempFile);
            }
            if (stagingDir != null && Files.exists(stagingDir)) {
                try (Stream<Path> walk = Files.walk(stagingDir)) {
                    walk.sorted(Comparator.reverseOrder()).forEach(path -> {
                        try {
                            Files.delete(path);
                        } catch (IOException e) {
                            logger.error("{}Impossible de supprimer {}", tidLogPrefix, path, e);
                        }
                    });
                }
                logger.info("{}Répertoire de transit '{}' nettoyé.", tidLogPrefix, stagingDir);
            }
        } catch (IOException ex) {
            logger.error("{}Erreur lors du nettoyage des ressources pour '{}'.", tidLogPrefix, stagingDir != null ? stagingDir.getFileName() : (tempFile != null ? tempFile.getFileName() : "inconnu"), ex);
        }
    }

    public void shutdown() {
        running.set(false);
        if (kafkaConsumer != null) {
            kafkaConsumer.wakeup();
        }
    }

    private void close() {
        if (cleanupScheduler != null) {
            cleanupScheduler.shutdownNow();
        }
        if (kafkaConsumer != null) {
            kafkaConsumer.close();
        }
        logger.info("Ressources du consommateur fermées.");
    }
}

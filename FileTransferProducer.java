package com.example.kafka.filetransfer.producer;

import com.example.kafka.filetransfer.kafka.KafkaChunkPublisher;
import com.example.kafka.filetransfer.model.FileChunkDto;
import com.example.kafka.filetransfer.model.TransferConfig;
import com.example.kafka.filetransfer.proto.FileChunkMessage;
import com.example.kafka.filetransfer.service.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.protobuf.ByteString;
import com.google.protobuf.StringValue;
import org.apache.kafka.clients.producer.RecordMetadata; // NOUVEL IMPORT
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Path;
import java.util.Base64;
import java.util.Iterator;
import java.util.UUID;
import java.util.concurrent.Future; // NOUVEL IMPORT
import java.util.stream.Collectors;

public class FileTransferProducer {

    private static final Logger logger = LoggerFactory.getLogger(FileTransferProducer.class);

    // ... (déclarations et constructeur inchangés) ...
    private final TransferConfig config;
    private final ManifestService manifestService;
    private final HashingService hashingService;
    private final CompressionService compressionService;
    private final CryptoService cryptoService;
    private final ChunkingService chunkingService;
    private final ObjectMapper jsonMapper = new ObjectMapper();

    public FileTransferProducer(TransferConfig config) {
        this.config = config;
        this.manifestService = new ManifestService();
        this.hashingService = new HashingService();
        this.compressionService = new CompressionService();
        this.cryptoService = new CryptoService(config);
        this.chunkingService = new ChunkingService(config.getChunkSize());
    }


    public void startTransfer(Path filePath) throws Exception {
        String transferId = UUID.randomUUID().toString();
        String tidLogPrefix = "[TID:" + transferId.substring(0, 8) + "] ";

        try (KafkaChunkPublisher publisher = new KafkaChunkPublisher(config)) {

            logger.info("{}Événement de transfert : [STATUS={}] - Démarrage du transfert pour le fichier '{}'", tidLogPrefix, "TRANSFER_STARTED", filePath.getFileName());

            // ... (logique de création du manifest, hash, etc. inchangée) ...
            FileChunkMessage.Builder manifest = manifestService.createManifest(filePath, transferId);
            String fullFileHash = hashingService.calculateFileHash(filePath, config.getHashAlgorithm());
            manifest.setFileHash(fullFileHash);
            logger.info("{}Hash du fichier complet calculé : {}", tidLogPrefix, fullFileHash);
            CryptoService.EncryptionResult encryptionResult = cryptoService.prepareEncryption();
            final int totalChunks = (int) Math.ceil((double) filePath.toFile().length() / config.getChunkSize());
            manifest.setTotalChunks(totalChunks);
            logger.info("{}Le fichier sera découpé en {} chunks.", tidLogPrefix, totalChunks);
            
            // NOUVEAU: Variables pour stocker les métadonnées de début et de fin.
            RecordMetadata firstChunkMetadata = null;
            RecordMetadata lastChunkMetadata;

            Iterator<byte[]> chunkIterator = chunkingService.iterateChunks(filePath);
            int chunkNumber = 0;
            while (chunkIterator.hasNext()) {
                chunkNumber++;
                byte[] chunkData = chunkIterator.next();
                String chunkHash = hashingService.calculateChunkHash(chunkData, config.getHashAlgorithm());
                byte[] processedData = processChunkData(chunkData, encryptionResult);
                FileChunkMessage.Builder chunkMessageBuilder = manifest.clone()
                        .setChunkNumber(chunkNumber)
                        .setChunkHash(chunkHash)
                        .setOriginalChunkSize(chunkData.length)
                        .setData(ByteString.copyFrom(processedData));
                byte[] payload = serialize(chunkMessageBuilder.build());
                String chunkDebugInfo = chunkNumber + "/" + totalChunks;

                // MODIFIÉ: Logique d'envoi synchrone/asynchrone
                if (chunkNumber == 1) {
                    // Envoi SYNCHRONE du premier chunk pour obtenir l'offset de début.
                    firstChunkMetadata = publisher.publishSync(transferId, payload).get();
                    logger.debug("{}Premier chunk {} publié (Offset: {}).", tidLogPrefix, chunkDebugInfo, firstChunkMetadata.offset());
                } else {
                    // Envoi ASYNCHRONE des chunks intermédiaires pour la performance.
                    publisher.publish(transferId, payload, chunkDebugInfo);
                    logger.debug("{}Chunk {} publié.", tidLogPrefix, chunkDebugInfo);
                }
            }

            logger.info("{}Tous les chunks de données ont été publiés. Envoi du message final.", tidLogPrefix);
            FileChunkMessage finalMessageProto = buildFinalMessage(manifest, encryptionResult);
            byte[] finalPayload = serialize(finalMessageProto);
            // L'envoi du message final est déjà SYNCHRONE.
            lastChunkMetadata = publisher.publishSync(transferId, finalPayload).get();
            logger.debug("{}Message final publié (Offset: {}).", tidLogPrefix, lastChunkMetadata.offset());


            logger.info("{}Événement de transfert : [STATUS={}] - Tous les chunks ont été envoyés avec succès.", tidLogPrefix, "TRANSFER_COMPLETED_PRODUCER");

            // NOUVEAU: Log final avec les informations d'offset.
            if (firstChunkMetadata != null) {
                logger.info("{}TRAÇABILITÉ: Le fichier '{}' a été écrit sur le topic '{}', partition {}, entre les offsets {} et {}.",
                        tidLogPrefix,
                        filePath.getFileName(),
                        firstChunkMetadata.topic(),
                        firstChunkMetadata.partition(),
                        firstChunkMetadata.offset(),
                        lastChunkMetadata.offset()
                );
            }
        }
    }
    
    // ... (Le reste de la classe est inchangé) ...
    private byte[] serialize(FileChunkMessage message) throws Exception {
        if (config.getSerializationFormat() == TransferConfig.SerializationFormat.JSON) {
            FileChunkDto dto = new FileChunkDto();
            dto.transferId = message.getTransferId();
            dto.fileName = message.getFileName();
            dto.fileSize = message.getFileSize();
            dto.totalChunks = message.getTotalChunks();
            dto.chunkNumber = message.getChunkNumber();
            dto.fileHash = message.getFileHash();
            dto.chunkHash = message.getChunkHash();
            dto.hashAlgorithm = message.getHashAlgorithm().name();
            dto.originalChunkSize = message.getOriginalChunkSize();
            dto.destinationPath = message.getDestinationPath();
            dto.mtimeEpoch = message.getMtimeEpoch();
            dto.atimeEpoch = message.getAtimeEpoch();
            if (message.hasBirthtimeEpoch()) {
                dto.birthtimeEpoch = message.getBirthtimeEpoch().getValue();
            }
            if (message.hasPosixPermissions()) {
                dto.posixPermissions = message.getPosixPermissions().getValue();
            }
            if (message.hasOwnerName()) {
                dto.ownerName = message.getOwnerName().getValue();
            }
            if (message.hasGroupName()) {
                dto.groupName = message.getGroupName().getValue();
            }
            if (!message.getWindowsAclsList().isEmpty()) {
                dto.windowsAcls = message.getWindowsAclsList().stream().map(protoAcl -> {
                    FileChunkDto.AclEntryDto aclDto = new FileChunkDto.AclEntryDto();
                    aclDto.type = protoAcl.getType();
                    aclDto.principal = protoAcl.getPrincipal();
                    aclDto.permissions = protoAcl.getPermissionsList();
                    aclDto.flags = protoAcl.getFlagsList();
                    return aclDto;
                }).collect(Collectors.toList());
            }
            dto.isFinalChunk = message.getIsFinalChunk();
            dto.compressionAlgorithm = message.getCompressionAlgorithm().name();
            dto.data = Base64.getEncoder().encodeToString(message.getData().toByteArray());
            if (message.hasEncryptionCipher()) {
                dto.encryptionCipher = message.getEncryptionCipher().getValue();
                dto.encryptedSymmetricKey = Base64.getEncoder().encodeToString(message.getEncryptedSymmetricKey().toByteArray());
            }
            return jsonMapper.writeValueAsBytes(dto);
        } else {
            return message.toByteArray();
        }
    }
    private byte[] processChunkData(byte[] originalData, CryptoService.EncryptionResult encryptionResult) throws Exception {
        byte[] compressedData = compressionService.compress(originalData, config.getCompressionAlgorithm());
        return cryptoService.encrypt(compressedData, encryptionResult);
    }
    private FileChunkMessage buildFinalMessage(FileChunkMessage.Builder manifest, CryptoService.EncryptionResult encryptionResult) {
        if (encryptionResult.isEncrypted()) {
            manifest.setEncryptedSymmetricKey(ByteString.copyFrom(encryptionResult.getEncryptedSymmetricKey()));
            manifest.setEncryptionCipher(StringValue.of(encryptionResult.getCipherName()));
        }
        return manifest.setIsFinalChunk(true).build();
    }
}
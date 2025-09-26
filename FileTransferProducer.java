package com.example.kafka.filetransfer.producer;

import com.example.kafka.filetransfer.kafka.KafkaChunkPublisher;
import com.example.kafka.filetransfer.model.FileChunkDto;
import com.example.kafka.filetransfer.model.TransferConfig;
import com.example.kafka.filetransfer.proto.CompressionAlgorithm; // <-- NOUVEL IMPORT
import com.example.kafka.filetransfer.proto.FileChunkMessage;
import com.example.kafka.filetransfer.service.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.google.protobuf.ByteString;
import com.google.protobuf.StringValue;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.Base64;
import java.util.Iterator;
import java.util.UUID;
import java.util.stream.Collectors;

public class FileTransferProducer {

    private static final Logger logger = LoggerFactory.getLogger(FileTransferProducer.class);

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
        
        Path fileToChunk = filePath;
        Path tempCompressedFile = null;

        try (KafkaChunkPublisher publisher = new KafkaChunkPublisher(config)) {
            logger.info("{}Événement de transfert : [STATUS={}] - Démarrage du transfert pour le fichier '{}'", tidLogPrefix, "TRANSFER_STARTED", filePath.getFileName());

            FileChunkMessage.Builder manifest = manifestService.createManifest(filePath, transferId);
            String fullFileHash = hashingService.calculateFileHash(filePath, config.getHashAlgorithm());
            manifest.setFileHash(fullFileHash);
            logger.info("{}Hash du fichier original calculé : {}", tidLogPrefix, fullFileHash);

            // =======================================================================
            // CORRECTION APPLIQUÉE ICI
            // =======================================================================
            // On définit l'algorithme de compression dans le manifeste DÈS LE DÉBUT.
            CompressionAlgorithm compressionAlgo = CompressionAlgorithm.valueOf(config.getCompressionAlgorithm().toUpperCase());
            manifest.setCompressionAlgorithm(compressionAlgo);
            // =======================================================================

            if (compressionAlgo != CompressionAlgorithm.NONE) {
                logger.info("{}Compression du fichier en cours (Algorithme: {})...", tidLogPrefix, config.getCompressionAlgorithm());
                byte[] originalBytes = Files.readAllBytes(filePath);
                byte[] compressedBytes = compressionService.compress(originalBytes, config.getCompressionAlgorithm());
                
                tempCompressedFile = Files.createTempFile("kafka-transfer-", ".compressed");
                Files.write(tempCompressedFile, compressedBytes, StandardOpenOption.WRITE);
                
                String compressedHash = hashingService.calculateFileHash(tempCompressedFile, config.getHashAlgorithm());
                manifest.setCompressedFileHash(StringValue.of(compressedHash));
                fileToChunk = tempCompressedFile;
                logger.info("{}Hash du fichier compressé calculé : {}", tidLogPrefix, compressedHash);
                logger.info("{}Taille après compression : {} octets (Ratio: {}%)", tidLogPrefix, compressedBytes.length, String.format("%.2f", (double)compressedBytes.length / originalBytes.length * 100));
            }

            CryptoService.EncryptionResult encryptionResult = cryptoService.prepareEncryption();
            final int totalChunks = (int) Math.ceil((double) Files.size(fileToChunk) / config.getChunkSize());
            manifest.setTotalChunks(totalChunks);
            logger.info("{}Le fichier sera découpé en {} chunks.", tidLogPrefix, totalChunks);

            Iterator<byte[]> chunkIterator = chunkingService.iterateChunks(fileToChunk);
            int chunkNumber = 0;
            while (chunkIterator.hasNext()) {
                chunkNumber++;
                byte[] chunkData = chunkIterator.next();
                String chunkHash = hashingService.calculateChunkHash(chunkData, config.getHashAlgorithm());
                byte[] processedData = cryptoService.encrypt(chunkData, encryptionResult);

                FileChunkMessage.Builder chunkMessageBuilder = manifest.clone()
                        .setChunkNumber(chunkNumber)
                        .setChunkHash(chunkHash)
                        .setOriginalChunkSize(chunkData.length)
                        .setData(ByteString.copyFrom(processedData));
                byte[] payload = serialize(chunkMessageBuilder.build());
                publisher.publish(transferId, payload, chunkNumber + "/" + totalChunks);
                logger.debug("{}Chunk {} publié.", tidLogPrefix, chunkNumber + "/" + totalChunks);
            }

            logger.info("{}Tous les chunks de données ont été publiés. Envoi du message final.", tidLogPrefix);
            FileChunkMessage finalMessageProto = buildFinalMessage(manifest, encryptionResult);
            byte[] finalPayload = serialize(finalMessageProto);
            publisher.publishSync(transferId, finalPayload).get();
            logger.info("{}Événement de transfert : [STATUS={}] - Tous les chunks ont été envoyés avec succès.", tidLogPrefix, "TRANSFER_COMPLETED_PRODUCER");
        } finally {
            if (tempCompressedFile != null) {
                Files.deleteIfExists(tempCompressedFile);
            }
        }
    }

    private byte[] serialize(FileChunkMessage message) throws Exception {
        if (config.getSerializationFormat() == TransferConfig.SerializationFormat.JSON) {
            FileChunkDto dto = new FileChunkDto();
            dto.transferId = message.getTransferId();
            dto.fileName = message.getFileName();
            dto.fileSize = message.getFileSize();
            dto.totalChunks = message.getTotalChunks();
            dto.chunkNumber = message.getChunkNumber();
            dto.originalChunkSize = message.getOriginalChunkSize();
            if (message.hasSourceHostname()) dto.sourceHostname = message.getSourceHostname().getValue();
            if (message.hasSourceUsername()) dto.sourceUsername = message.getSourceUsername().getValue();
            dto.hashAlgorithm = message.getHashAlgorithm().name();
            dto.fileHash = message.getFileHash();
            dto.chunkHash = message.getChunkHash();
            dto.compressionAlgorithm = message.getCompressionAlgorithm().name();
            if (message.hasCompressedFileHash()) dto.compressedFileHash = message.getCompressedFileHash().getValue();
            dto.data = Base64.getEncoder().encodeToString(message.getData().toByteArray());
            dto.destinationPath = message.getDestinationPath();
            dto.mtimeEpoch = message.getMtimeEpoch();
            dto.atimeEpoch = message.getAtimeEpoch();
            if (message.hasBirthtimeEpoch()) dto.birthtimeEpoch = message.getBirthtimeEpoch().getValue();
            if (message.hasPosixPermissions()) dto.posixPermissions = message.getPosixPermissions().getValue();
            if (message.hasOwnerName()) dto.ownerName = message.getOwnerName().getValue();
            if (message.hasGroupName()) dto.groupName = message.getGroupName().getValue();
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
            if (message.hasEncryptionCipher()) {
                dto.encryptionCipher = message.getEncryptionCipher().getValue();
                dto.encryptedSymmetricKey = Base64.getEncoder().encodeToString(message.getEncryptedSymmetricKey().toByteArray());
            }
            return jsonMapper.writeValueAsBytes(dto);
        } else {
            return message.toByteArray();
        }
    }

    private FileChunkMessage buildFinalMessage(FileChunkMessage.Builder manifest, CryptoService.EncryptionResult encryptionResult) {
        if (encryptionResult.isEncrypted()) {
            manifest.setEncryptedSymmetricKey(ByteString.copyFrom(encryptionResult.getEncryptedSymmetricKey()));
            manifest.setEncryptionCipher(StringValue.of(encryptionResult.getCipherName()));
        }
        return manifest.setIsFinalChunk(true).build();
    }
}

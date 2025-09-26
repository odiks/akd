package com.example.kafka.filetransfer.model;

import com.fasterxml.jackson.annotation.JsonProperty; // <-- NOUVEL IMPORT

import java.util.List;

/**
 * DTO (Data Transfer Object) représentant un chunk de fichier pour la sérialisation JSON.
 * CORRIGÉ : Utilise @JsonProperty pour garantir la compatibilité avec le format JSON de Protobuf.
 */
public class FileChunkDto {

    // --- Métadonnées de Transfert ---
    @JsonProperty("transfer_id")
    public String transferId;
    @JsonProperty("file_name")
    public String fileName;
    @JsonProperty("file_size")
    public long fileSize;
    @JsonProperty("total_chunks")
    public int totalChunks;
    @JsonProperty("chunk_number")
    public int chunkNumber;
    @JsonProperty("original_chunk_size")
    public int originalChunkSize;

    // --- Métadonnées de Provenance ---
    @JsonProperty("source_hostname")
    public String sourceHostname;
    @JsonProperty("source_username")
    public String sourceUsername;

    // --- Métadonnées d'Intégrité ---
    @JsonProperty("hash_algorithm")
    public String hashAlgorithm;
    @JsonProperty("file_hash")
    public String fileHash;
    @JsonProperty("chunk_hash")
    public String chunkHash;

    // --- Métadonnées de Compression ---
    @JsonProperty("compression_algorithm")
    public String compressionAlgorithm;
    @JsonProperty("compressed_file_hash")
    public String compressedFileHash;

    // --- Données (encodées en Base64) ---
    public String data;

    // --- Métadonnées de Reconstruction ---
    @JsonProperty("destination_path")
    public String destinationPath;
    @JsonProperty("mtime_epoch")
    public long mtimeEpoch;
    @JsonProperty("atime_epoch")
    public long atimeEpoch;
    @JsonProperty("birthtime_epoch")
    public Long birthtimeEpoch;

    // --- Permissions ---
    @JsonProperty("posix_permissions")
    public String posixPermissions;
    @JsonProperty("owner_name")
    public String ownerName;
    @JsonProperty("group_name")
    public String groupName;
    @JsonProperty("windows_acls")
    public List<AclEntryDto> windowsAcls;

    // --- Options de Transfert ---
    @JsonProperty("is_final_chunk")
    public boolean isFinalChunk;

    // --- Sécurité ---
    @JsonProperty("encryption_cipher")
    public String encryptionCipher;
    @JsonProperty("encrypted_symmetric_key")
    public String encryptedSymmetricKey;

    public static class AclEntryDto {
        public String type;
        public String principal;
        public List<String> permissions;
        public List<String> flags;
    }
}
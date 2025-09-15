package com.example.kafkafs;

import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.clients.producer.RecordMetadata;
import org.apache.kafka.common.serialization.ByteArraySerializer;
import org.apache.kafka.common.serialization.StringSerializer;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.file.*;
import java.nio.file.attribute.*;
import java.security.KeyFactory;
import java.security.KeyPairGenerator;
import java.security.PublicKey;
import java.security.spec.X509EncodedKeySpec;
import java.util.*;
import java.util.concurrent.Future;

public class ProducerCLI {

    private static Properties defaultProducerProps(Options opts){
        Properties p = new Properties();
        p.put("acks", opts.enableAcksAll ? "all" : "1");
        p.put("enable.idempotence", String.valueOf(opts.idempotentProducer));
        p.put("max.in.flight.requests.per.connection", String.valueOf(opts.maxInFlightRequests));
        p.put("request.timeout.ms", String.valueOf((int)opts.requestTimeout.toMillis()));
        p.put("delivery.timeout.ms", String.valueOf((int)opts.deliveryTimeout.toMillis()));
        return p;
    }

    private static void usage(){
        System.err.println("Usage: producer --bootstrap <brokers> --topic <topic> --file <path> [--dest <destPath>]");
        System.err.println("       [--hash SHA256|SHA384|SHA512] [--sign RSA|ED25519|ECDSA|NONE] [--comp LZ4|GZIP|NONE]");
        System.err.println("       [--enc AES256_GCM_RSA_OAEP|NONE] [--chunk <bytes>] [--pubkey <Base64X509>]");
        System.err.println("       [--tx true|false] [--manifest-topic <topic>]");
        // --- MISE À JOUR USAGE ---
        System.err.println("       [--ssl-truststore-location <path>] [--ssl-truststore-password <pass>]");
        System.err.println("       [--ssl-keystore-location <path>] [--ssl-keystore-password <pass>] [--ssl-key-password <pass>]");
    }

    public static void main(String[] args) {
        Map<String, String> m = parseArgs(args);
        if (!m.containsKey("--bootstrap") || !m.containsKey("--topic") || !m.containsKey("--file")) {
            usage();
            System.exit(ErrorCode.VALIDATION_ERROR.code());
        }
        Options opts = Options.defaults();
        if (m.containsKey("--hash")) opts.hashAlgo = Options.HashAlgo.valueOf(m.get("--hash"));
        if (m.containsKey("--sign")) opts.signAlgo = Options.SignAlgo.valueOf(m.get("--sign"));
        if (m.containsKey("--comp")) opts.compression = Options.Compression.valueOf(m.get("--comp"));
        if (m.containsKey("--enc")) opts.encryption = Options.Encryption.valueOf(m.get("--enc"));
        if (m.containsKey("--chunk")) opts.chunkSizeBytes = Integer.parseInt(m.get("--chunk"));

        Path path = Paths.get(m.get("--file"));
        String dest = m.getOrDefault("--dest", path.toString());

        Properties props = defaultProducerProps(opts);
        props.put("bootstrap.servers", m.get("--bootstrap"));

        // --- DEBUT DES MODIFICATIONS SSL ---
        if (m.containsKey("--ssl-truststore-location")) {
            props.put("security.protocol", "SSL");
            props.put("ssl.truststore.location", m.get("--ssl-truststore-location"));
            if (m.containsKey("--ssl-truststore-password")) {
                props.put("ssl.truststore.password", m.get("--ssl-truststore-password"));
            }
        }
        if (m.containsKey("--ssl-keystore-location")) {
            props.put("ssl.keystore.location", m.get("--ssl-keystore-location"));
            if (m.containsKey("--ssl-keystore-password")) {
                props.put("ssl.keystore.password", m.get("--ssl-keystore-password"));
            }
            if (m.containsKey("--ssl-key-password")) {
                props.put("ssl.key.password", m.get("--ssl-key-password"));
            }
        }
        // --- FIN DES MODIFICATIONS SSL ---

        boolean tx = Boolean.parseBoolean(m.getOrDefault("--tx", "false"));
        String manifestTopic = m.getOrDefault("--manifest-topic", m.get("--topic") + "-manifest");

        try (KafkaProducer<String, byte[]> producer = new KafkaProducer<>(props, new StringSerializer(), new ByteArraySerializer())){

            byte[] fileBytes = Files.readAllBytes(path);
            long totalChunks = (fileBytes.length + opts.chunkSizeBytes - 1) / opts.chunkSizeBytes;

            Metadata base = Metadata.base(path.getFileName().toString(), fileBytes.length, totalChunks, dest, opts);
            // collect filesystem metadata (best effort)
            try {
                BasicFileAttributes bfa = Files.readAttributes(path, BasicFileAttributes.class);
                base.mtimeEpoch = bfa.lastModifiedTime().toMillis()/1000;
                base.atimeEpoch = bfa.lastAccessTime().toMillis()/1000;
                base.ctimeEpoch = bfa.lastModifiedTime().toMillis()/1000;
                base.birthtimeEpoch = bfa.creationTime().toMillis()/1000;
            } catch (Exception ignored){}
            try {
                PosixFileAttributes pfa = Files.readAttributes(path, PosixFileAttributes.class);
                base.user = pfa.owner().getName();
                base.group = pfa.group().getName();
                base.posixPermissions = Metadata.permsToSymbolic(pfa.permissions());
            } catch (Exception ignored){}

            base.fileHashHex = HashUtil.toHex(HashUtil.digest(fileBytes, opts.hashAlgo));

            PublicKey rsaPub = null;
            if (opts.encryption == Options.Encryption.AES256_GCM_RSA_OAEP) {
                if (!m.containsKey("--pubkey")) {
                    System.err.println("Encryption selected but --pubkey missing (Base64 X509)");
                    System.exit(ErrorCode.CONFIG_ERROR.code());
                }
                try {
                    byte[] der = Base64.getDecoder().decode(m.get("--pubkey"));
                    rsaPub = KeyFactory.getInstance("RSA").generatePublic(new X509EncodedKeySpec(der));
                    base.publicKeyInfo = m.get("--pubkey");
                    base.cipherInfo = "AES-256-GCM + RSA-OAEP(SHA-256)";
                } catch (Exception e) {
                    e.printStackTrace();
                    System.exit(ErrorCode.CONFIG_ERROR.code());
                }
            }

            if (tx) {
                props.put("transactional.id", "kft-" + base.transferId);
                try (KafkaProducer<String, byte[]> ignored = new KafkaProducer<>(props, new StringSerializer(), new ByteArraySerializer())) {}
            }

            if (tx) producer.initTransactions();
            if (tx) producer.beginTransaction();

            List<Future<RecordMetadata>> futures = new ArrayList<>();
            List<String> chunkHashes = new ArrayList<>();

            for (int i=0;i<totalChunks;i++){
                int start = i * opts.chunkSizeBytes;
                int end = Math.min(fileBytes.length, start + opts.chunkSizeBytes);
                byte[] slice = java.util.Arrays.copyOfRange(fileBytes, start, end);
                chunkHashes.add(HashUtil.toHex(HashUtil.digest(slice, opts.hashAlgo)));

                byte[] processed = slice;
                try {
                    processed = CompressionUtil.compress(processed, opts.compression);
                } catch (IOException ioe){
                    ioe.printStackTrace();
                    if (tx) producer.abortTransaction();
                    System.exit(ErrorCode.IO_ERROR.code());
                }

                byte[] payload = processed;
                byte[] wrapped = null;
                if (opts.encryption == Options.Encryption.AES256_GCM_RSA_OAEP) {
                    try {
                        var enc = CryptoUtil.encryptAesGcmRsaOaep(processed, rsaPub);
                        byte[] iv = enc.iv();
                        payload = java.nio.ByteBuffer.allocate(12 + enc.ciphertext().length).put(iv).put(enc.ciphertext()).array();
                        wrapped = enc.wrappedKey();
                    } catch (Exception e) {
                        e.printStackTrace();
                        if (tx) producer.abortTransaction();
                        System.exit(ErrorCode.CRYPTO_ERROR.code());
                    }
                }

                Metadata md = baseCopy(base);
                md.chunkNumber = i;
                md.chunkHashHex = chunkHashes.get(i);
                md.encryptedKey = wrapped;

                String metaJson;
                try { metaJson = Metadata.toJson(md); }
                catch (Exception e){ e.printStackTrace(); if (tx) producer.abortTransaction(); System.exit(ErrorCode.IO_ERROR.code()); return; }

                String key = base.routingKey;
                ProducerRecord<String, byte[]> rec = new ProducerRecord<>(m.get("--topic"), key, pack(metaJson, payload));
                futures.add(producer.send(rec));
            }

            // EOF marker
            Metadata eof = baseCopy(base);
            eof.endOfFile = true;
            if (opts.signAlgo != Options.SignAlgo.NONE) {
                try {
                    KeyPairGenerator kpg = switch (opts.signAlgo) {
                        case ED25519 -> KeyPairGenerator.getInstance("Ed25519");
                        case ECDSA -> KeyPairGenerator.getInstance("EC");
                        case RSA -> KeyPairGenerator.getInstance("RSA");
                        default -> KeyPairGenerator.getInstance("Ed25519");
                    };
                    if (opts.signAlgo == Options.SignAlgo.RSA) kpg.initialize(3072);
                    eof.signature = CryptoUtil.sign(hexToBytes(base.fileHashHex), opts.signAlgo, kpg.generateKeyPair());
                } catch (Exception e) {
                    e.printStackTrace();
                    if (tx) producer.abortTransaction();
                    System.exit(ErrorCode.CRYPTO_ERROR.code());
                }
            }
            try {
                futures.add(producer.send(new ProducerRecord<>(m.get("--topic"), base.routingKey, pack(Metadata.toJson(eof), new byte[0]))));
            } catch (Exception e){
                e.printStackTrace();
                if (tx) producer.abortTransaction();
                System.exit(ErrorCode.KAFKA_ERROR.code());
            }

            // Manifest Avro to manifest topic
            try {
                byte[] man = ManifestAvro.serialize(base.transferId, base.routingKey, base, chunkHashes);
                futures.add(producer.send(new ProducerRecord<>(manifestTopic, base.routingKey, man)));
            } catch (Exception e){
                e.printStackTrace();
                if (tx) producer.abortTransaction();
                System.exit(ErrorCode.IO_ERROR.code());
            }

            for (Future<RecordMetadata> f: futures) { try { f.get(); } catch (Exception ignored) {} }
            producer.flush();
            if (tx) producer.commitTransaction();
            System.exit(ErrorCode.OK.code());
        } catch (Exception e){
            e.printStackTrace();
            System.exit(ErrorCode.UNKNOWN.code());
        }
    }

    private static Map<String, String> parseArgs(String[] args){
        Map<String, String> m = new HashMap<>();
        for (int i=0;i<args.length;i++){
            if (args[i].startsWith("--")){
                if (i+1<args.length && !args[i+1].startsWith("--")) m.put(args[i], args[i+1]);
                else m.put(args[i], "true");
            }
        }
        return m;
    }

    private static Metadata baseCopy(Metadata b){
        Metadata c = new Metadata();
        c.transferId = b.transferId;
        c.routingKey = b.routingKey;
        c.fileName = b.fileName;
        c.fileSize = b.fileSize;
        c.totalChunks = b.totalChunks;
        c.destinationPath = b.destinationPath;
        c.mtimeEpoch = b.mtimeEpoch;
        c.atimeEpoch = b.atimeEpoch;
        c.ctimeEpoch = b.ctimeEpoch;
        c.birthtimeEpoch = b.birthtimeEpoch;
        c.uid = b.uid; c.gid=b.gid; c.sid=b.sid; c.user=b.user; c.group=b.group;
        c.posixPermissions=b.posixPermissions; c.windowsPermissions=b.windowsPermissions;
        c.acl=b.acl; c.xattr=b.xattr; c.selinuxContext=b.selinuxContext;
        c.fileHashHex=b.fileHashHex;
        c.optionsDescriptor=b.optionsDescriptor;
        c.publicKeyInfo=b.publicKeyInfo; c.cipherInfo=b.cipherInfo;
        return c;
    }

    private static byte[] pack(String metaJson, byte[] payload) {
        byte[] mj = metaJson.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        ByteBuffer bb = ByteBuffer.allocate(4 + mj.length + payload.length);
        bb.putInt(mj.length).put(mj).put(payload);
        return bb.array();
    }

    private static byte[] hexToBytes(String s){
        int len = s.length();
        byte[] data = new byte[len/2];
        for (int i=0;i<len;i+=2) data[i/2] = (byte)((Character.digit(s.charAt(i),16)<<4)+Character.digit(s.charAt(i+1),16));
        return data;
    }
}

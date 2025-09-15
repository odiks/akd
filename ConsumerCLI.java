package com.example.kafkafs;

import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.serialization.ByteArrayDeserializer;
import org.apache.kafka.common.serialization.StringDeserializer;

import java.nio.ByteBuffer;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributeView;
import java.nio.file.attribute.FileTime;
import java.security.KeyFactory;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

public class ConsumerCLI {

    private static Properties defaultConsumerProps(Options opts){
        Properties p = new Properties();
        p.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, "false");
        p.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        p.put(ConsumerConfig.ISOLATION_LEVEL_CONFIG, "read_committed");
        p.put(ConsumerConfig.MAX_POLL_RECORDS_CONFIG, "500");
        p.put(ConsumerConfig.MAX_POLL_INTERVAL_MS_CONFIG, "300000");
        p.put(ConsumerConfig.SESSION_TIMEOUT_MS_CONFIG, "10000");
        p.put(ConsumerConfig.REQUEST_TIMEOUT_MS_CONFIG, String.valueOf((int)opts.requestTimeout.toMillis()));
        return p;
    }

    private static void usage(){
        System.err.println("Usage: consumer --bootstrap <brokers> --group <groupId> --topic <topic> --outdir <dir> [--privkey <Base64PKCS8>] [--signpub <Base64X509>]");
        // --- MISE À JOUR USAGE ---
        System.err.println("       [--ssl-truststore-location <path>] [--ssl-truststore-password <pass>]");
        System.err.println("       [--ssl-keystore-location <path>] [--ssl-keystore-password <pass>] [--ssl-key-password <pass>]");
    }

    public static void main(String[] args) {
        Map<String,String> m = parseArgs(args);
        if (!m.containsKey("--bootstrap") || !m.containsKey("--group") || !m.containsKey("--topic") || !m.containsKey("--outdir")){
            usage();
            System.exit(ErrorCode.VALIDATION_ERROR.code());
        }
        Options opts = Options.defaults(); // consumer respects producer options through metadata

        Properties props = defaultConsumerProps(opts);
        props.put("bootstrap.servers", m.get("--bootstrap"));
        props.put("group.id", m.get("--group"));

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

        PrivateKey rsaPriv = null;
        if (m.containsKey("--privkey")) {
            try {
                byte[] der = Base64.getDecoder().decode(m.get("--privkey"));
                rsaPriv = KeyFactory.getInstance("RSA").generatePrivate(new PKCS8EncodedKeySpec(der));
            } catch (Exception e) {
                e.printStackTrace();
                System.exit(ErrorCode.CONFIG_ERROR.code());
            }
        }
        PublicKey signPub = null;
        if (m.containsKey("--signpub")) {
            try {
                byte[] der = Base64.getDecoder().decode(m.get("--signpub"));
                signPub = KeyFactory.getInstance("Ed25519").generatePublic(new X509EncodedKeySpec(der)); // default
            } catch (Exception e) {
                e.printStackTrace();
                System.exit(ErrorCode.CONFIG_ERROR.code());
            }
        }

        final PrivateKey finalRsaPriv = rsaPriv;
        final PublicKey finalSignPub = signPub;

        try (KafkaConsumer<String, byte[]> consumer = new KafkaConsumer<>(props, new StringDeserializer(), new ByteArrayDeserializer())){
            consumer.subscribe(List.of(m.get("--topic")));

            Map<String, Assembler> assemblers = new ConcurrentHashMap<>();

            while (true){
                ConsumerRecords<String, byte[]> records = consumer.poll(Duration.ofMillis(2000));
                if (records.isEmpty()) continue;

                for (ConsumerRecord<String, byte[]> rec: records){
                    try {
                        ByteBuffer bb = ByteBuffer.wrap(rec.value());
                        int metaLen = bb.getInt();
                        byte[] mj = new byte[metaLen];
                        bb.get(mj);
                        byte[] payload = new byte[bb.remaining()];
                        bb.get(payload);

                        Metadata md = Metadata.fromJson(new String(mj, java.nio.charset.StandardCharsets.UTF_8));
                        Options effective = parseOptions(md.optionsDescriptor);

                        Assembler as = assemblers.computeIfAbsent(md.transferId, k -> new Assembler(md, Paths.get(m.get("--outdir")), effective, finalRsaPriv, finalSignPub));
                        if (md.endOfFile) {
                            as.finish(md);
                        } else {
                            as.acceptChunk(md, payload);
                        }
                    } catch (Exception e){
                        e.printStackTrace();
                        System.exit(ErrorCode.IO_ERROR.code());
                    }
                }
                consumer.commitSync();
            }
        } catch (Exception e){
            e.printStackTrace();
            System.exit(ErrorCode.KAFKA_ERROR.code());
        }
    }

    static class Assembler {
        final Path checkpointPath;
        final Metadata base;
        final Path outDir;
        final Options opts;
        final PrivateKey rsaPriv;
        final PublicKey signPub;
        final BitSet received;
        final Map<Long, byte[]> chunks = new HashMap<>();

        Assembler(Metadata base, Path outDir, Options opts, PrivateKey rsaPriv, PublicKey signPub){
            this.base = base;
            this.outDir = outDir;
            this.opts = opts;
            this.rsaPriv = rsaPriv;
            this.signPub = signPub;
            this.received = new BitSet((int)base.totalChunks);
            this.checkpointPath = outDir.resolve(base.transferId + ".ckpt");
            loadCheckpoint();
        }

        void acceptChunk(Metadata md, byte[] payload) throws Exception {
            byte[] data = payload;
            if (opts.encryption == Options.Encryption.AES256_GCM_RSA_OAEP && md.encryptedKey != null && rsaPriv != null) {
                byte[] iv = Arrays.copyOfRange(payload, 0, 12);
                byte[] ct = Arrays.copyOfRange(payload, 12, payload.length);
                data = CryptoUtil.decryptAesGcmRsaOaep(iv, ct, md.encryptedKey, rsaPriv);
            }
            // decompress
            data = CompressionUtil.decompress(data, opts.compression);
            // verify per-chunk hash
            String h = HashUtil.toHex(HashUtil.digest(data, opts.hashAlgo));
            if (!h.equals(md.chunkHashHex)){
                System.err.println("Chunk hash mismatch for " + md.fileName + " #" + md.chunkNumber);
                System.exit(ErrorCode.INTEGRITY_FAILED.code());
            }
            chunks.put(md.chunkNumber, data);
            saveCheckpoint((int)md.chunkNumber);
            received.set((int)md.chunkNumber);
        }


        void loadCheckpoint(){
            try {
                if (Files.exists(checkpointPath)) {
                    for (String line: Files.readAllLines(checkpointPath)) {
                        int idx = Integer.parseInt(line.trim());
                        received.set(idx);
                    }
                }
            } catch (Exception ignored){}
        }
        void saveCheckpoint(int idx){
            try {
                Files.writeString(checkpointPath, idx + System.lineSeparator(),
                        java.nio.file.StandardOpenOption.CREATE, java.nio.file.StandardOpenOption.APPEND);
            } catch (Exception ignored){}
        }

        void finish(Metadata md) throws Exception {
            if (opts.requireAllChunks) {
                if (received.cardinality() != base.totalChunks) {
                    System.err.println("Missing chunks: expected " + base.totalChunks + ", got " + received.cardinality());
                    System.exit(ErrorCode.INTEGRITY_FAILED.code());
                }
            }
            // assemble
            Path outPath = outDir.resolve(base.destinationPath);
            Files.createDirectories(outPath.getParent() == null ? outDir : outPath.getParent());
            try (java.io.OutputStream os = Files.newOutputStream(outPath, StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE)){
                for (long i=0;i<base.totalChunks;i++){
                    if (chunks.containsKey(i)) {
                        os.write(chunks.get(i));
                    }
                }
                if (opts.fsyncOnClose) os.flush();
            }
            // verify whole-file hash
            byte[] assembled = Files.readAllBytes(outPath);
            String h = HashUtil.toHex(HashUtil.digest(assembled, opts.hashAlgo));
            if (!h.equals(base.fileHashHex)) {
                System.err.println("File hash mismatch after assembly");
                System.exit(ErrorCode.INTEGRITY_FAILED.code());
            }
            // verify signature if provided
            if (opts.verifySignature && md.signature != null && signPub != null) {
                boolean ok = CryptoUtil.verify(hexToBytes(base.fileHashHex), md.signature, opts.signAlgo, signPub);
                if (!ok) {
                    System.err.println("Signature verification failed");
                    System.exit(ErrorCode.INTEGRITY_FAILED.code());
                }
            }
            // restore timestamps
            try {
                BasicFileAttributeView view = Files.getFileAttributeView(outPath, BasicFileAttributeView.class);
                view.setTimes(FileTime.fromMillis(md.mtimeEpoch*1000), FileTime.fromMillis(md.atimeEpoch*1000), null);
            } catch (Exception ignored){}
            try { Files.deleteIfExists(checkpointPath); } catch (Exception ignored){}
            System.out.println("Reconstructed: " + outPath);
            System.exit(ErrorCode.OK.code());
        }
    }

    private static Options parseOptions(String desc){
        Options o = Options.defaults();
        if (desc == null) return o;
        for (String kv: desc.split(";")){
            if (!kv.contains("=")) continue;
            String[] p = kv.split("=",2);
            try {
                switch (p[0]){
                    case "hash" -> o.hashAlgo = Options.HashAlgo.valueOf(p[1]);
                    case "sign" -> o.signAlgo = Options.SignAlgo.valueOf(p[1]);
                    case "comp" -> o.compression = Options.Compression.valueOf(p[1]);
                    case "enc" -> o.encryption = Options.Encryption.valueOf(p[1]);
                    case "chunk" -> o.chunkSizeBytes = Integer.parseInt(p[1]);
                    case "verifySig" -> o.verifySignature = Boolean.parseBoolean(p[1]);
                    case "fsync" -> o.fsyncOnClose = Boolean.parseBoolean(p[1]);
                }
            } catch (Exception ignored){}
        }
        return o;
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

    private static byte[] hexToBytes(String s){
        int len = s.length();
        byte[] data = new byte[len/2];
        for (int i=0;i<len;i+=2) data[i/2] = (byte)((Character.digit(s.charAt(i),16)<<4)+Character.digit(s.charAt(i+1),16));
        return data;
    }
}

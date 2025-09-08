# receive_file_consumer.py

import os
import sys
import json
import base64
import hashlib
from pathlib import Path
from confluent_kafka import Consumer, KafkaException

# ======================= CONFIGURATION =======================
# À MODIFIER SELON VOTRE ENVIRONNEMENT KAFKA
KAFKA_BROKERS = 'localhost:9093'  # Adresse et PORT SSL de votre broker
KAFKA_TOPIC = 'file-transfers'
KAFKA_CONSUMER_GROUP = 'file-transfer-group-1'
OUTPUT_DIRECTORY = "output_files"  # Les fichiers reçus seront stockés ici

# CHEMINS VERS VOS CERTIFICATS SSL
SSL_CA_LOCATION = './certs/ca.crt'
SSL_CERT_LOCATION = './certs/client.crt'
SSL_KEY_LOCATION = './certs/client.key'
# =============================================================

class FileReassembler:
    """Gère l'état et la reconstruction de plusieurs transferts en parallèle."""
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.active_transfers = {}
        print(f"Consumer initialisé. Répertoire de sortie : {self.output_dir.resolve()}")

    def process_message(self, msg):
        """Aiguille le message vers le bon gestionnaire."""
        try:
            data = json.loads(msg.value().decode('utf-8'))
            msg_type = data['message_type']
            transfer_id = data.get('transfer_id')
            if not transfer_id: return
        except (json.JSONDecodeError, KeyError):
            return

        if msg_type == 'FILE_METADATA_START':
            self.handle_start(transfer_id, data)
        elif msg_type == 'FILE_CHUNK' and transfer_id in self.active_transfers:
            self.handle_chunk(transfer_id, data)
        elif msg_type == 'FILE_TRANSFER_END' and transfer_id in self.active_transfers:
            self.handle_end(transfer_id, data)

    def handle_start(self, transfer_id, data):
        """Initialise la structure pour un nouveau transfert."""
        if transfer_id in self.active_transfers: return
            
        metadata = data['file_metadata']
        filename = metadata['filename']
        temp_path = self.output_dir / f"{filename}.{transfer_id}.part"
        
        try:
            # Allouer l'espace disque immédiatement pour éviter les problèmes
            with open(temp_path, "wb") as f:
                f.truncate(metadata['size_bytes'])

            self.active_transfers[transfer_id] = {
                "metadata": metadata,
                "temp_path": temp_path,
                "temp_file_handle": open(temp_path, "r+b"),
                "received_chunks": set(),
                "total_chunks": metadata['total_chunks']
            }
            print(f"\n▶️  Démarrage du transfert pour '{filename}' (ID: {transfer_id})")
        except IOError as e:
            print(f"Erreur critique : Impossible de créer le fichier temporaire {temp_path}. Erreur : {e}")

    def handle_chunk(self, transfer_id, data):
        """Écrit un chunk de données à sa position exacte dans le fichier temporaire."""
        transfer = self.active_transfers[transfer_id]
        chunk_index = data['chunk_index']
        
        if chunk_index not in transfer['received_chunks']:
            try:
                chunk_data = base64.b64decode(data['chunk_data_base64'])
                offset = chunk_index * (transfer['metadata']['size_bytes'] // transfer['total_chunks']) if transfer['total_chunks'] > 1 else 0
                
                # Le seek est plus robuste si des chunks étaient traités dans le désordre
                transfer['temp_file_handle'].seek(offset)
                transfer['temp_file_handle'].write(chunk_data)
                transfer['received_chunks'].add(chunk_index)
                
                progress = len(transfer['received_chunks']) * 100 / transfer['total_chunks']
                sys.stdout.write(f"\r   - Réception de '{transfer['metadata']['filename']}': {progress:.2f}%...")
                sys.stdout.flush()
            except (IOError, TypeError) as e:
                print(f"Erreur lors de l'écriture du chunk {chunk_index} pour {transfer_id}. Erreur: {e}")

    def handle_end(self, transfer_id, data):
        """Finalise, valide et nettoie un transfert."""
        sys.stdout.write("\n") # Nouvelle ligne après la barre de progression
        print(f"🏁 Fin de transfert reçue pour '{self.active_transfers[transfer_id]['metadata']['filename']}'. Validation en cours...")
        transfer = self.active_transfers[transfer_id]
        transfer['temp_file_handle'].close()

        if len(transfer['received_chunks']) != transfer['total_chunks']:
            print(f"❌ ÉCHEC (Complétude) : Reçu {len(transfer['received_chunks'])}/{transfer['total_chunks']} chunks.")
            os.remove(transfer['temp_path'])
            del self.active_transfers[transfer_id]
            return
            
        hasher = hashlib.sha256()
        with open(transfer['temp_path'], 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        
        if hasher.hexdigest() != data['final_hash_sha256']:
            print(f"❌ ÉCHEC (Intégrité) : Le hash du fichier ne correspond pas.")
            os.remove(transfer['temp_path'])
            del self.active_transfers[transfer_id]
            return

        final_path = self.output_dir / transfer['metadata']['filename']
        os.rename(transfer['temp_path'], final_path)
        self.apply_metadata(final_path, transfer['metadata'])
        print(f"✅ SUCCÈS : Fichier '{final_path.name}' reconstruit et validé.")
        del self.active_transfers[transfer_id]
        
    def apply_metadata(self, file_path, metadata):
        """Tente d'appliquer les métadonnées de base au fichier final."""
        try:
            ts = metadata['timestamps_utc_epoch']
            os.utime(file_path, (ts['access_time'], ts['modified_time']))

            if os.name == 'posix' and metadata.get('unix_permissions'):
                os.chmod(file_path, int(metadata['unix_permissions']['mode'], 8))
        except Exception as e:
            print(f"Avertissement : Impossible d'appliquer les métadonnées à {file_path.name}. Erreur : {e}")

    def run(self):
        """Boucle principale du consumer qui écoute les messages Kafka."""
        conf = {
            'bootstrap.servers': KAFKA_BROKERS,
            'group.id': KAFKA_CONSUMER_GROUP,
            'auto.offset.reset': 'earliest',
            'enable.auto.commit': True,
            'security.protocol': 'SSL',
            'ssl.ca.location': SSL_CA_LOCATION,
            'ssl.certificate.location': SSL_CERT_LOCATION,
            'ssl.key.location': SSL_KEY_LOCATION,
        }

        for path in [SSL_CA_LOCATION, SSL_CERT_LOCATION, SSL_KEY_LOCATION]:
            if not os.path.exists(path):
                print(f"Erreur Critique : Le fichier de certificat '{path}' est introuvable.")
                sys.exit(1)

        consumer = Consumer(conf)
        try:
            consumer.subscribe([KAFKA_TOPIC])
            print(f"Consumer démarré. En écoute sur le topic '{KAFKA_TOPIC}'...")
            while True:
                msg = consumer.poll(timeout=1.0)
                if msg is None: continue
                if msg.error():
                    if msg.error().code() != KafkaException._PARTITION_EOF:
                        print(msg.error())
                    continue
                self.process_message(msg)
        except KeyboardInterrupt:
            print("\nArrêt du consumer demandé.")
        finally:
            consumer.close()
            print("Consumer arrêté proprement.")

if __name__ == "__main__":
    reassembler = FileReassembler(OUTPUT_DIRECTORY)
    reassembler.run()

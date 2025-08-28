# consumer.py
import os
import hashlib
import shutil
from confluent_kafka import Consumer, KafkaError

# =============================================================================
# --- CONFIGURATION ---
# À modifier selon votre environnement
# =============================================================================

# Adresse des brokers Kafka avec le port SSL
KAFKA_BROKERS = "kafka-broker1:9093,kafka-broker2:9093"
KAFKA_TOPIC = "file-transfer-topic"
CONSUMER_GROUP_ID = "file-receiver-group-1"

# !! IMPORTANT !!
# Ces répertoires doivent être sur un stockage partagé (ex: NFS)
# si vous lancez plusieurs instances du consommateur.
TEMP_ASSEMBLY_DIR = "/mnt/shared_storage/kafka_assembly"
FINAL_DESTINATION_DIR = "/var/data/received_files"

# Configuration SSL/TLS
# Remplacez les chemins par les vôtres.
SSL_CONFIG = {
    'security.protocol': 'SSL',
    'ssl.ca.location': '/path/to/certs/ca.pem',
    'ssl.certificate.location': '/path/to/certs/service.cert.pem',
    'ssl.key.location': '/path/to/certs/service.key.pem',
}

# (Optionnel) Gérer le mot de passe de la clé privée via une variable d'environnement
key_password = os.getenv("KAFKA_KEY_PASSWORD")
if key_password:
    SSL_CONFIG['ssl.key.password'] = key_password

# =============================================================================

# Dictionnaire en mémoire pour suivre l'état des fichiers en cours d'assemblage
assembly_state = {}

def process_chunk(msg):
    """
    Traite un chunk reçu, l'écrit sur disque et finalise le fichier si complet.
    """
    try:
        headers = {key: value.decode('utf-8') for key, value in msg.headers()}
        file_id = headers.get("file_id")
        
        if not file_id:
            print("Message ignoré (pas de file_id dans les headers).")
            return

        # Initialiser l'état si c'est le premier chunk de ce fichier
        if file_id not in assembly_state:
            assembly_state[file_id] = {"metadata": headers}
            print(f"Début de l'assemblage pour le fichier '{headers['file_name']}' (ID: {file_id})")

        temp_file_path = os.path.join(TEMP_ASSEMBLY_DIR, file_id)
        with open(temp_file_path, "ab") as f:
            f.write(msg.value())

        # Vérifier si c'est le dernier chunk
        if headers.get("is_last_chunk") == 'true':
            print(f"Dernier chunk reçu pour '{headers['file_name']}'. Vérification de l'intégrité...")
            
            # 1. Calculer le checksum du fichier réassemblé
            with open(temp_file_path, 'rb') as f:
                reassembled_hash = hashlib.sha256(f.read()).hexdigest()
            
            original_hash = headers["file_checksum"]

            # 2. Comparer les checksums
            if reassembled_hash == original_hash:
                print(f"Checksum OK pour {file_id}. Fichier validé.")
                
                # 3. Déplacer le fichier vers sa destination finale
                final_path = os.path.join(FINAL_DESTINATION_DIR, headers["file_name"])
                shutil.move(temp_file_path, final_path)
                print(f"Fichier '{headers['file_name']}' déplacé avec succès vers '{final_path}'")
            else:
                print(f"ERREUR CHECKSUM pour {file_id}. Fichier corrompu.")
                print(f"  -> Attendu: {original_hash}")
                print(f"  -> Obtenu:  {reassembled_hash}")
                os.remove(temp_file_path)
            
            # 4. Nettoyer l'état
            del assembly_state[file_id]

    except Exception as e:
        print(f"Une erreur est survenue lors du traitement d'un chunk: {e}")

def main():
    """
    Fonction principale du consommateur.
    """
    print("Démarrage du consommateur de fichiers...")
    os.makedirs(TEMP_ASSEMBLY_DIR, exist_ok=True)
    os.makedirs(FINAL_DESTINATION_DIR, exist_ok=True)

    consumer_conf = {
        'bootstrap.servers': KAFKA_BROKERS,
        'group.id': CONSUMER_GROUP_ID,
        'auto.offset.reset': 'earliest',
        'enable.auto.commit': False  # Commit manuel pour plus de fiabilité
    }
    consumer_conf.update(SSL_CONFIG)

    consumer = Consumer(consumer_conf)
    consumer.subscribe([KAFKA_TOPIC])

    try:
        while True:
            msg = consumer.poll(timeout=1.0) # Attendre 1 seconde pour un message
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    # Fin de la partition, pas une erreur
                    continue
                else:
                    print(f"Erreur Kafka: {msg.error()}")
                    break
            
            process_chunk(msg)
            consumer.commit(asynchronous=True) # Confirmer que le message a été traité

    except KeyboardInterrupt:
        print("Arrêt du consommateur demandé.")
    finally:
        print("Fermeture du consommateur.")
        consumer.close()

if __name__ == "__main__":
    main()

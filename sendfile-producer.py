# producer.py
import os
import uuid
import hashlib
from confluent_kafka import Producer

# =============================================================================
# --- CONFIGURATION ---
# À modifier selon votre environnement
# =============================================================================

# Adresse des brokers Kafka avec le port SSL (généralement 9093)
KAFKA_BROKERS = "kafka-broker1:9093,kafka-broker2:9093"
KAFKA_TOPIC = "file-transfer-topic"

# Taille des chunks en octets (1 MB)
CHUNK_SIZE = 1 * 1024 * 1024

# Configuration SSL/TLS
# Remplacez les chemins par les vôtres.
SSL_CONFIG = {
    'security.protocol': 'SSL',
    'ssl.ca.location': '/path/to/certs/ca.pem',                 # Certificat de l'autorité de certification (Truststore)
    'ssl.certificate.location': '/path/to/certs/service.cert.pem', # Certificat public du client (Keystore)
    'ssl.key.location': '/path/to/certs/service.key.pem',       # Clé privée du client (Keystore)
}

# (Optionnel) Gérer le mot de passe de la clé privée via une variable d'environnement
# Pour l'utiliser, exécutez : export KAFKA_KEY_PASSWORD="votre_mot_de_passe"
key_password = os.getenv("KAFKA_KEY_PASSWORD")
if key_password:
    SSL_CONFIG['ssl.key.password'] = key_password

# =============================================================================

def delivery_report(err, msg):
    """Callback pour suivre le statut de l'envoi des messages."""
    if err is not None:
        print(f"ERREUR: Échec de l'envoi du message: {err}")
    else:
        print(f"Message envoyé -> Topic: {msg.topic()} Partition: [{msg.partition()}] Offset: {msg.offset()}")

def send_file_to_kafka(file_path):
    """
    Lit, segmente et envoie un fichier à Kafka.
    """
    if not os.path.exists(file_path):
        print(f"ERREUR: Le fichier '{file_path}' n'existe pas.")
        return

    file_id = str(uuid.uuid4())
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    
    print(f"Préparation de l'envoi du fichier '{file_name}' (ID: {file_id})...")
    
    # Calculer le checksum global du fichier
    with open(file_path, 'rb') as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()

    producer_conf = {
        'bootstrap.servers': KAFKA_BROKERS,
        'acks': 'all',  # Garantit la plus haute durabilité
        'message.max.bytes': CHUNK_SIZE + 4096 # Marge pour les headers
    }
    producer_conf.update(SSL_CONFIG)
    
    producer = Producer(producer_conf)

    chunk_sequence_number = 0
    with open(file_path, 'rb') as f:
        while True:
            chunk_data = f.read(CHUNK_SIZE)
            if not chunk_data:
                break # Fin du fichier

            chunk_checksum = hashlib.sha256(chunk_data).hexdigest()
            is_last = len(chunk_data) < CHUNK_SIZE

            headers = [
                ("file_id", file_id.encode('utf-8')),
                ("file_name", file_name.encode('utf-8')),
                ("total_file_size", str(file_size).encode('utf-8')),
                ("chunk_sequence_number", str(chunk_sequence_number).encode('utf-8')),
                ("chunk_checksum", chunk_checksum.encode('utf-8')),
                ("file_checksum", file_hash.encode('utf-8')),
                ("is_last_chunk", b'true' if is_last else b'false')
            ]
            
            # Utiliser file_id comme clé pour garantir l'ordre des chunks par partition
            producer.produce(
                KAFKA_TOPIC,
                key=file_id.encode('utf-8'),
                value=chunk_data,
                headers=headers,
                callback=delivery_report
            )
            producer.poll(0) # Permet de déclencher les callbacks en arrière-plan
            chunk_sequence_number += 1
            
    print("Tous les chunks ont été mis en file d'attente. Envoi final en cours...")
    producer.flush() # Attendre que tous les messages soient envoyés
    print(f"Fichier '{file_name}' envoyé avec succès en {chunk_sequence_number} chunks.")

if __name__ == "__main__":
    # --- Exemple d'utilisation ---
    # 1. Crée un fichier de test
    TEST_FILE = "fichier_a_envoyer.txt"
    print(f"Création du fichier de test '{TEST_FILE}'...")
    with open(TEST_FILE, "w") as f:
        f.write("Ceci est un test pour l'envoi de fichier via Kafka de manière sécurisée.\n")
        f.write("." * 2 * 1024 * 1024) # ~2MB de données
        f.write("\nFin du fichier.")

    # 2. Envoyer le fichier
    send_file_to_kafka(TEST_FILE)
    
    # 3. Nettoyage
    os.remove(TEST_FILE)

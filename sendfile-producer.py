import os
import uuid
import json
import hashlib
from confluent_kafka import Producer

# Configuration Kafka
KAFKA_BROKERS = "localhost:9092"
KAFKA_TOPIC = "file-transfer-topic"
CHUNK_SIZE = 1 * 1024 * 1024  # 1 MB

def delivery_report(err, msg):
    """Callback appelé sur l'achèvement ou l'échec de l'envoi du message."""
    if err is not None:
        print(f"Échec de l'envoi du message : {err}")
    else:
        print(f"Message envoyé au topic {msg.topic()} [{msg.partition()}] à l'offset {msg.offset()}")

def send_file_to_kafka(file_path):
    file_id = str(uuid.uuid4())
    file_name = os.path.basename(file_path)
    file_size = os.path.getsize(file_path)
    
    # Calculer le checksum global du fichier (peut prendre du temps pour les gros fichiers)
    with open(file_path, 'rb') as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()

    producer_conf = {
        'bootstrap.servers': KAFKA_BROKERS,
        'acks': 'all',  # Assure que le message est écrit sur tous les réplicas synchronisés
        'message.max.bytes': CHUNK_SIZE + 2048 # S'assurer que le chunk et les headers passent
    }
    producer = Producer(producer_conf)

    chunk_sequence_number = 0
    with open(file_path, 'rb') as f:
        while True:
            chunk_data = f.read(CHUNK_SIZE)
            if not chunk_data:
                break # Fin du fichier

            chunk_checksum = hashlib.sha256(chunk_data).hexdigest()

            # Métadonnées à envoyer avec le chunk
            # Nous pouvons les mettre dans les headers ou les sérialiser avec le chunk_data si nécessaire
            headers = [
                ("file_id", file_id.encode('utf-8')),
                ("file_name", file_name.encode('utf-8')),
                ("total_file_size", str(file_size).encode('utf-8')),
                ("chunk_sequence_number", str(chunk_sequence_number).encode('utf-8')),
                ("chunk_size", str(len(chunk_data)).encode('utf-8')),
                ("chunk_checksum", chunk_checksum.encode('utf-8')),
                ("file_checksum", file_hash.encode('utf-8')),
                ("is_last_chunk", b'true' if len(chunk_data) < CHUNK_SIZE else b'false') # Indiquer le dernier chunk
            ]
            
            # Utilisation de file_id comme clé pour s'assurer que tous les chunks du même fichier vont à la même partition
            producer.produce(
                KAFKA_TOPIC,
                key=file_id.encode('utf-8'),
                value=chunk_data,
                headers=headers,
                callback=delivery_report
            )
            producer.poll(0) # Permet au callback d'être appelé

            chunk_sequence_number += 1
            
    # S'assurer que tous les messages sont envoyés avant de quitter
    producer.flush()
    print(f"Fichier '{file_name}' ({file_id}) envoyé en {chunk_sequence_number} chunks.")

# Exemple d'utilisation
if __name__ == "__main__":
    # Crée un fichier de test
    with open("test_file.txt", "w") as f:
        f.write("Ceci est un fichier de test pour Kafka.\n")
        for i in range(10000):
            f.write(f"Ligne de données numéro {i:05d}.\n")
    
    send_file_to_kafka("test_file.txt")
    
    # Nettoyage
    os.remove("test_file.txt")

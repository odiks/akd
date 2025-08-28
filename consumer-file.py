import os
import hashlib
import shutil
from confluent_kafka import Consumer, KafkaError

# Configuration
KAFKA_BROKERS = "localhost:9092"
KAFKA_TOPIC = "file-transfer-topic"
CONSUMER_GROUP_ID = "file-receiver-group"
TEMP_ASSEMBLY_DIR = "/tmp/kafka_file_assembly"
FINAL_DESTINATION_DIR = "/var/data/received_files"

# Créer les répertoires si ils n'existent pas
os.makedirs(TEMP_ASSEMBLY_DIR, exist_ok=True)
os.makedirs(FINAL_DESTINATION_DIR, exist_ok=True)

# Pour suivre l'état des fichiers en cours de réassemblage
# Structure: { "file_id": {"total_chunks": 42, "received_chunks": {0, 1, 2, ...}, "metadata": {...}} }
assembly_state = {}

def process_chunk(msg):
    headers_dict = {key: value.decode('utf-8') for key, value in msg.headers()}
    
    file_id = headers_dict.get("file_id")
    if not file_id:
        print("Message reçu sans file_id, ignoré.")
        return

    # Initialiser l'état pour ce fichier si c'est le premier chunk
    if file_id not in assembly_state:
        # Extraire le nombre total de chunks (on le fait une seule fois)
        # Pour cela, on peut se baser sur le dernier chunk qui aura un flag "is_last_chunk"
        # Ou on peut calculer le total_chunks = ceil(total_file_size / chunk_size)
        total_file_size = int(headers_dict['total_file_size'])
        # La taille de chunk est celle du producer, sauf pour le dernier.
        # Pour la simplicité, supposons que le producer nous envoie total_chunks
        # (à ajouter dans le producer)
        # total_chunks = int(headers_dict.get("total_chunks", 0))

        assembly_state[file_id] = {
            "received_chunks": set(),
            "metadata": headers_dict
        }
        print(f"Début de l'assemblage pour le fichier : {headers_dict['file_name']} ({file_id})")

    state = assembly_state[file_id]
    chunk_sequence_number = int(headers_dict["chunk_sequence_number"])
    
    # Écrire le chunk sur le disque
    temp_file_path = os.path.join(TEMP_ASSEMBLY_DIR, file_id)
    with open(temp_file_path, "ab") as f:
        # Note: L'écriture séquentielle simple ne fonctionne que si les messages arrivent dans l'ordre.
        # Grâce à la clé `file_id`, c'est garanti par partition.
        # Pour une robustesse absolue, il faudrait utiliser f.seek() pour écrire à l'offset exact.
        # offset = chunk_sequence_number * CHUNK_SIZE_FROM_PRODUCER
        # f.seek(offset)
        f.write(msg.value())

    # Mettre à jour l'état
    state["received_chunks"].add(chunk_sequence_number)
    
    # Vérifier si le fichier est complet
    # Pour cela, il nous faut le nombre total de chunks. On peut l'envoyer dans les métadonnées.
    # Disons qu'on l'a ajouté dans le producer :
    # is_last_chunk = headers_dict.get("is_last_chunk") == 'true'
    # if is_last_chunk:
    #     # La condition de complétion est de recevoir le dernier chunk et d'avoir tous les autres.
    #     # C'est un peu complexe. Simplifions : nous avons besoin de `total_chunks`.
    #     # Il faudrait l'ajouter dans les headers de chaque message.
    
    # Imaginons que le producer envoie "is_last_chunk"
    if headers_dict.get("is_last_chunk") == 'true':
        print(f"Dernier chunk reçu pour {file_id}. Vérification de l'intégrité...")
        
        # 1. Validation de l'intégrité
        with open(temp_file_path, 'rb') as f:
            reassembled_hash = hashlib.sha256(f.read()).hexdigest()
        
        original_hash = state["metadata"]["file_checksum"]

        if reassembled_hash == original_hash:
            print(f"Checksum OK pour {file_id}.")
            
            # 2. Déplacer le fichier vers la destination finale
            final_path = os.path.join(FINAL_DESTINATION_DIR, state["metadata"]["file_name"])
            shutil.move(temp_file_path, final_path)
            print(f"Fichier '{state['metadata']['file_name']}' déplacé vers {final_path}")
            
            # 3. Nettoyer l'état
            del assembly_state[file_id]
        else:
            print(f"ERREUR CHECKSUM pour {file_id}. Fichier corrompu. Attendu: {original_hash}, Obtenu: {reassembled_hash}")
            # Gérer l'échec : supprimer le fichier temp, logger l'erreur, etc.
            os.remove(temp_file_path)
            del assembly_state[file_id]

def main():
    consumer_conf = {
        'bootstrap.servers': KAFKA_BROKERS,
        'group.id': CONSUMER_GROUP_ID,
        'auto.offset.reset': 'earliest',
        'enable.auto.commit': False # Très important pour ce cas d'usage !
    }
    consumer = Consumer(consumer_conf)
    consumer.subscribe([KAFKA_TOPIC])

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
                    print(msg.error())
                    break
            
            process_chunk(msg)
            
            # Commit de l'offset manuellement APRÈS avoir traité le message
            consumer.commit(asynchronous=True)

    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()

if __name__ == "__main__":
    main()

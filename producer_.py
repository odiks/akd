# send_file_producer.py

import os
import sys
import uuid
import hashlib
import json
import base64
from pathlib import Path
from confluent_kafka import Producer

# ======================= CONFIGURATION =======================
# À MODIFIER SELON VOTRE ENVIRONNEMENT KAFKA
KAFKA_BROKERS = 'localhost:9093'  # Adresse et PORT SSL de votre broker
KAFKA_TOPIC = 'file-transfers'

# CHEMINS VERS VOS CERTIFICATS SSL
SSL_CA_LOCATION = './certs/ca.crt'
SSL_CERT_LOCATION = './certs/client.crt'
SSL_KEY_LOCATION = './certs/client.key'
# =============================================================

def get_file_metadata(file_path_str: str) -> dict:
    """Collecte les métadonnées d'un fichier de manière multiplateforme."""
    file_path = Path(file_path_str)
    stat_info = file_path.stat()
    
    metadata = {
        "filename": file_path.name,
        "size_bytes": stat_info.st_size,
        "timestamps_utc_epoch": {
            "modified_time": stat_info.st_mtime,
            "access_time": stat_info.st_atime,
            "metadata_change_time": stat_info.st_ctime,
            "creation_time": getattr(stat_info, 'st_birthtime', stat_info.st_ctime)
        },
        "unix_permissions": None,
        "windows_permissions": None
    }

    if os.name == 'posix':
        import grp, pwd
        metadata["unix_permissions"] = {
            "mode": oct(stat_info.st_mode & 0o777),
            "uid": stat_info.st_uid,
            "gid": stat_info.st_gid,
            "owner": pwd.getpwuid(stat_info.st_uid).pw_name,
            "group": grp.getgrgid(stat_info.st_gid).gr_name,
        }
    elif os.name == 'nt':
        try:
            import win32security
            sd = win32security.GetFileSecurity(file_path_str, win32security.OWNER_SECURITY_INFORMATION)
            owner_sid = sd.GetSecurityDescriptorOwner()
            metadata["windows_permissions"] = {"owner_sid": str(owner_sid)}
        except Exception as e:
            print(f"Avertissement : Impossible de lire les métadonnées de sécurité Windows : {e}")
    return metadata

def delivery_report(err, msg):
    """Callback pour vérifier que les messages ont bien été envoyés."""
    if err is not None:
        print(f'Erreur lors de l\'envoi du message : {err}')

def transfer_file(producer, topic, file_path, chunk_size=1024*512): # 512KB
    """Orchestre le découpage et l'envoi du fichier et de ses métadonnées."""
    if not Path(file_path).is_file():
        print(f"Erreur : '{file_path}' n'est pas un fichier valide ou est inaccessible.")
        return

    print(f"▶️  Début du transfert pour le fichier : {file_path}")
    transfer_id = str(uuid.uuid4())
    metadata = get_file_metadata(file_path)

    print("   - Calcul du hash SHA256 du fichier...")
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    file_hash = hasher.hexdigest()
    
    total_chunks = (metadata['size_bytes'] + chunk_size - 1) // chunk_size
    metadata['file_hash_sha256'] = file_hash
    metadata['total_chunks'] = total_chunks
    
    # *** CORRECTION IMPORTANTE ***
    # Le producer doit informer le consumer de la taille des chunks qu'il utilise.
    metadata['chunk_size'] = chunk_size

    start_message = {
        "message_type": "FILE_METADATA_START",
        "transfer_id": transfer_id,
        "source_path": str(Path(file_path).resolve()),
        "file_metadata": metadata
    }
    producer.produce(topic, key=transfer_id.encode('utf-8'), value=json.dumps(start_message).encode('utf-8'), callback=delivery_report)
    print(f"   - Métadonnées envoyées (ID de transfert: {transfer_id})")

    chunk_count = 0
    with open(file_path, 'rb') as f:
        for i in range(total_chunks):
            chunk_data = f.read(chunk_size)
            chunk_message = {
                "message_type": "FILE_CHUNK",
                "transfer_id": transfer_id,
                "chunk_index": i,
                "chunk_data_base64": base64.b64encode(chunk_data).decode('ascii')
            }
            producer.produce(topic, key=transfer_id.encode('utf-8'), value=json.dumps(chunk_message).encode('utf-8'), callback=delivery_report)
            chunk_count += 1
            producer.poll(0)

    print(f"   - {chunk_count}/{total_chunks} chunks de données envoyés.")

    end_message = {
        "message_type": "FILE_TRANSFER_END",
        "transfer_id": transfer_id,
        "final_chunk_count": total_chunks,
        "final_hash_sha256": file_hash
    }
    producer.produce(topic, key=transfer_id.encode('utf-8'), value=json.dumps(end_message).encode('utf-8'), callback=delivery_report)
    print("   - Message de fin de transfert envoyé.")
    
    print("   - Finalisation de l'envoi...")
    producer.flush()
    print(f"✅ Transfert de '{Path(file_path).name}' terminé avec succès.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Utilisation : python send_file_producer.py <chemin_du_fichier>")
        sys.exit(1)
        
    file_to_send = sys.argv[1]

    conf = {
        'bootstrap.servers': KAFKA_BROKERS,
        'security.protocol': 'SSL',
        'ssl.ca.location': SSL_CA_LOCATION,
        'ssl.certificate.location': SSL_CERT_LOCATION,
        'ssl.key.location': SSL_KEY_LOCATION,
        'enable.idempotence': True,
        'compression.type': 'gzip' # Optionnel mais recommandé pour la performance
    }
    
    for path in [SSL_CA_LOCATION, SSL_CERT_LOCATION, SSL_KEY_LOCATION]:
        if not os.path.exists(path):
            print(f"Erreur Critique : Le fichier de certificat '{path}' est introuvable.")
            sys.exit(1)

    kafka_producer = Producer(conf)
    transfer_file(kafka_producer, KAFKA_TOPIC, file_to_send)

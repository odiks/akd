# Librairies recommandées
import os
import uuid
import hashlib
import json
import base64
from pathlib import Path
from confluent_kafka import Producer # Robuste et performant

# Pour les métadonnées spécifiques
if os.name == 'nt':
    import win32security
    import pywintypes

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
            # birthtime n'est pas toujours disponible
            "creation_time": getattr(stat_info, 'st_birthtime', stat_info.st_ctime)
        },
        "unix_permissions": None,
        "windows_permissions": None
    }

    if os.name == 'posix':
        import grp
        import pwd
        metadata["unix_permissions"] = {
            "mode": oct(stat_info.st_mode & 0o777),
            "uid": stat_info.st_uid,
            "gid": stat_info.st_gid,
            "owner": pwd.getpwuid(stat_info.st_uid).pw_name,
            "group": grp.getgrgid(stat_info.st_gid).gr_name,
            # La gestion des ACLs et SELinux nécessite des libs externes (ex: pylibacl, xattr)
            # et des appels subprocess, ce qui est plus complexe.
        }
    elif os.name == 'nt':
        try:
            sd = win32security.GetFileSecurity(file_path_str, win32security.OWNER_SECURITY_INFORMATION | win32security.GROUP_SECURITY_INFORMATION | win32security.DACL_SECURITY_INFORMATION)
            owner_sid = sd.GetSecurityDescriptorOwner()
            group_sid = sd.GetSecurityDescriptorGroup()
            dacl = sd.GetSecurityDescriptorDacl()

            metadata["windows_permissions"] = {
                "owner_sid": str(owner_sid),
                "group_sid": str(group_sid),
                "dacl_sddl": sd.GetSecurityDescriptorSddl(win32security.DACL_SECURITY_INFORMATION)
            }
        except pywintypes.error as e:
            print(f"Could not get Windows security info for {file_path_str}: {e}")


    return metadata

def transfer_file(producer, topic, file_path, chunk_size=1024*512): # 512KB chunks
    """
    Fonction principale du producer pour transférer un fichier.
    """
    if not Path(file_path).is_file():
        print(f"Error: {file_path} is not a valid file.")
        return

    # 1. Générer ID et collecter les métadonnées
    transfer_id = str(uuid.uuid4())
    metadata = get_file_metadata(file_path)

    # 2. Calculer le hash du fichier entier
    hasher = hashlib.sha256()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            hasher.update(chunk)
    file_hash = hasher.hexdigest()
    
    metadata['file_hash_sha256'] = file_hash
    metadata['total_chunks'] = (metadata['size_bytes'] + chunk_size - 1) // chunk_size

    # 3. Envoyer le message de métadonnées (FILE_METADATA_START)
    start_message = {
        "message_type": "FILE_METADATA_START",
        "transfer_id": transfer_id,
        "source_path": file_path,
        "file_metadata": metadata
    }
    producer.produce(topic, key=transfer_id.encode('utf-8'), value=json.dumps(start_message).encode('utf-8'))

    # 4. Lire et envoyer les chunks du fichier (FILE_CHUNK)
    with open(file_path, 'rb') as f:
        for i in range(metadata['total_chunks']):
            chunk_data = f.read(chunk_size)
            chunk_message = {
                "message_type": "FILE_CHUNK",
                "transfer_id": transfer_id,
                "chunk_index": i,
                "chunk_data_base64": base64.b64encode(chunk_data).decode('ascii')
            }
            producer.produce(topic, key=transfer_id.encode('utf-8'), value=json.dumps(chunk_message).encode('utf-8'))
            producer.poll(0) # Permet de gérer l'envoi en arrière-plan

    # 5. Envoyer le message de fin (FILE_TRANSFER_END)
    end_message = {
        "message_type": "FILE_TRANSFER_END",
        "transfer_id": transfer_id,
        "final_chunk_count": metadata['total_chunks'],
        "final_hash_sha256": file_hash
    }
    producer.produce(topic, key=transfer_id.encode('utf-8'), value=json.dumps(end_message).encode('utf-8'))
    
    producer.flush() # Attendre la fin de l'envoi de tous les messages
    print(f"Successfully sent file {file_path} with transfer ID {transfer_id}")

# Librairies recommandées
import os
import json
import base64
import hashlib
from confluent_kafka import Consumer

class FileReassembler:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.active_transfers = {} # Dictionnaire pour suivre les transferts en cours

    def process_message(self, msg):
        try:
            data = json.loads(msg.value().decode('utf-8'))
            transfer_id = data['transfer_id']
            msg_type = data['message_type']
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Error decoding message: {e}")
            return

        if msg_type == 'FILE_METADATA_START':
            self.handle_start(transfer_id, data)
        elif msg_type == 'FILE_CHUNK':
            self.handle_chunk(transfer_id, data)
        elif msg_type == 'FILE_TRANSFER_END':
            self.handle_end(transfer_id, data)

    def handle_start(self, transfer_id, data):
        if transfer_id in self.active_transfers:
            print(f"Warning: Received duplicate start for transfer {transfer_id}")
            return
            
        metadata = data['file_metadata']
        filename = metadata['filename']
        temp_path = self.output_dir / f"{filename}.{transfer_id}.part"
        
        self.active_transfers[transfer_id] = {
            "metadata": metadata,
            "temp_path": str(temp_path),
            "temp_file_handle": open(temp_path, "wb"),
            "received_chunks": set(),
            "total_chunks": metadata['total_chunks']
        }
        print(f"Starting new transfer {transfer_id} for file {filename}")

    def handle_chunk(self, transfer_id, data):
        if transfer_id not in self.active_transfers:
            # On pourrait mettre en cache au cas où le chunk arrive avant les métadonnées
            return
            
        transfer = self.active_transfers[transfer_id]
        chunk_index = data['chunk_index']
        
        if chunk_index not in transfer['received_chunks']:
            chunk_data = base64.b64decode(data['chunk_data_base64'])
            # Se positionner au bon endroit dans le fichier temporaire
            offset = chunk_index * (transfer['metadata']['size_bytes'] // transfer['total_chunks'])
            transfer['temp_file_handle'].seek(offset)
            transfer['temp_file_handle'].write(chunk_data)
            transfer['received_chunks'].add(chunk_index)

    def handle_end(self, transfer_id, data):
        if transfer_id not in self.active_transfers:
            return

        transfer = self.active_transfers[transfer_id]
        transfer['temp_file_handle'].close()

        # a. Contrôle de la réception de tous les chunks
        if len(transfer['received_chunks']) != transfer['total_chunks']:
            print(f"Error for {transfer_id}: Missing chunks. Expected {transfer['total_chunks']}, got {len(transfer['received_chunks'])}")
            os.remove(transfer['temp_path']) # Nettoyage
            del self.active_transfers[transfer_id]
            return
            
        # b. Contrôle de l'intégrité du fichier
        hasher = hashlib.sha256()
        with open(transfer['temp_path'], 'rb') as f:
            while chunk := f.read(8192):
                hasher.update(chunk)
        
        reconstructed_hash = hasher.hexdigest()
        expected_hash = data['final_hash_sha256']

        if reconstructed_hash != expected_hash:
            print(f"Error for {transfer_id}: Hash mismatch!")
            os.remove(transfer['temp_path'])
            del self.active_transfers[transfer_id]
            return

        # c. Reconstruction et application des métadonnées
        final_path = self.output_dir / transfer['metadata']['filename']
        os.rename(transfer['temp_path'], final_path)
        
        # Option pour la gestion des métadonnées
        self.apply_metadata(final_path, transfer['metadata'])

        print(f"Successfully reconstructed file {final_path} for transfer {transfer_id}")
        del self.active_transfers[transfer_id]
        
    def apply_metadata(self, file_path, metadata):
        """Applique les métadonnées au fichier reconstruit."""
        # Timestamps
        ts = metadata['timestamps_utc_epoch']
        os.utime(file_path, (ts['access_time'], ts['modified_time']))

        # Permissions - C'est la partie la plus délicate
        if os.name == 'posix' and metadata['unix_permissions']:
            perms = metadata['unix_permissions']
            try:
                # Changer le propriétaire nécessite des privilèges root
                os.chown(file_path, perms['uid'], perms['gid'])
            except PermissionError:
                print("Warning: Could not change ownership. Insufficient privileges.")
            os.chmod(file_path, int(perms['mode'], 8))
        
        elif os.name == 'nt' and metadata['windows_permissions']:
            # L'application des ACLs via pywin32 est complexe et nécessite des privilèges.
            # C'est un point à développer spécifiquement.
            print("Warning: Applying Windows ACLs is an advanced feature and not fully implemented.")

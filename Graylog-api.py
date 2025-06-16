import requests
import configparser
import json
import sys

class GraylogAPI:
    """
    Classe pour interagir avec l'API de Graylog.
    Gère l'authentification, la pagination et les appels API de base.
    """
    def __init__(self, config_file='config.ini'):
        """
        Initialise le client API en lisant le fichier de configuration.
        """
        config = configparser.ConfigParser()
        config.read(config_file)

        try:
            self.base_url = config['graylog']['url']
            self.token = config['graylog']['token']
        except KeyError:
            print(f"Erreur: Assurez-vous que le fichier '{config_file}' existe et contient une section [graylog] avec 'url' et 'token'.")
            sys.exit(1)

        # Utilisation d'une session pour persister les headers
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Requested-By': 'PythonGraylogClient'
        })
        # L'authentification Basic Auth avec le token comme nom d'utilisateur et "token" comme mot de passe est la méthode recommandée.
        self.session.auth = (self.token, 'token')

    def _make_request(self, method, endpoint, params=None, data=None):
        """Méthode privée pour effectuer des requêtes et gérer les erreurs."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, params=params, json=data)
            response.raise_for_status()  # Lève une exception pour les codes d'erreur HTTP (4xx ou 5xx)
            return response.json()
        except requests.exceptions.HTTPError as e:
            print(f"Erreur HTTP: {e.response.status_code} pour l'URL {url}")
            print(f"Réponse: {e.response.text}")
        except requests.exceptions.ConnectionError as e:
            print(f"Erreur de connexion à l'URL {url}: {e}")
        except requests.exceptions.RequestException as e:
            print(f"Une erreur est survenue: {e}")
        return None

    def _get_paginated_results(self, endpoint, key_name):
        """
        Récupère toutes les pages d'un résultat paginé.
        - endpoint: Le point d'API à appeler (ex: '/users')
        - key_name: La clé dans la réponse JSON qui contient la liste des objets (ex: 'users')
        """
        all_items = []
        page = 1
        per_page = 50  # Taille de page standard dans Graylog
        
        while True:
            params = {'page': page, 'per_page': per_page}
            data = self._make_request('GET', endpoint, params=params)
            
            if not data or not data.get(key_name):
                break # Arrête la boucle si pas de données ou si la clé n'existe pas
                
            items = data[key_name]
            all_items.extend(items)
            
            # Si on a reçu moins d'éléments que la taille de la page, c'est la dernière page.
            if len(items) < per_page:
                break
                
            page += 1
            
        return all_items

    def get_streams(self):
        """Récupère la liste de tous les streams."""
        print("Récupération de la liste des streams...")
        # L'endpoint des streams n'est généralement pas paginé de la même manière.
        # Il renvoie un objet avec une clé 'streams'.
        data = self._make_request('GET', '/streams')
        return data.get('streams', []) if data else []

    def get_users(self):
        """Récupère la liste de tous les utilisateurs."""
        print("Récupération de la liste des utilisateurs...")
        return self._get_paginated_results('/users', 'users')

    def get_stream_permissions(self, stream_id):
        """
        Récupère les permissions pour un stream donné.
        Les permissions sont gérées via les entités "shares".
        """
        # La permission est liée à l'entité de type "stream" avec son ID
        # GRN = Graylog Resource Name
        entity_grn = f"grn::::stream:{stream_id}"
        endpoint = f"/authz/shares/entities/{entity_grn}"
        data = self._make_request('GET', endpoint)
        return data.get('grants', []) if data else []

def main():
    """Fonction principale du script."""
    api = GraylogAPI()

    # --- 1. Lister les streams ---
    streams = api.get_streams()
    if not streams:
        print("Aucun stream trouvé ou erreur lors de la récupération.")
        return

    print("\n" + "="*50)
    print(f"Total des streams trouvés : {len(streams)}")
    print("="*50)
    for stream in streams:
        print(f"- ID: {stream['id']} | Titre: {stream['title']}")

    # --- 2. Lister les utilisateurs ---
    users = api.get_users()
    if not users:
        print("Aucun utilisateur trouvé ou erreur lors de la récupération.")
        return
        
    print("\n" + "="*50)
    print(f"Total des utilisateurs trouvés : {len(users)}")
    print("="*50)
    for user in users:
        print(f"- Username: {user['username']} | Full Name: {user['full_name']} | Email: {user['email']}")

    # --- 3. Lister les permissions par stream ---
    print("\n" + "="*50)
    print("Permissions sur les Streams")
    print("="*50)
    for stream in streams:
        print(f"\n--- Stream : '{stream['title']}' (ID: {stream['id']}) ---")
        permissions = api.get_stream_permissions(stream['id'])
        if permissions:
            for perm in permissions:
                # 'grantee' est l'ID de l'utilisateur ou du rôle/équipe
                # 'capability' est le rôle (ex: 'viewer', 'manager')
                grantee = perm['grantee']
                capability = perm['capability']
                print(f"  -> Rôle '{capability}' accordé à '{grantee}'")
        else:
            print("  -> Aucune permission spécifique trouvée pour ce stream.")


if __name__ == "__main__":
    main()

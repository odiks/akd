
# ... (dans la classe GraylogAPI) ...

    def grant_user_to_stream(self, username, stream_id, role='viewer'):
        """
        Assigne un utilisateur à un stream avec un rôle spécifique.

        :param username: Le nom d'utilisateur Graylog.
        :param stream_id: L'ID du stream.
        :param role: Le rôle à assigner ('viewer', 'manager', 'owner'). Par défaut 'viewer'.
        :return: True en cas de succès, False sinon.
        """
        print(f"Tentative d'assignation de l'utilisateur '{username}' au stream '{stream_id}' avec le rôle '{role}'...")

        # 1. Construire le GRN de l'entité à partager (le stream)
        stream_grn = f"grn::::stream:{stream_id}"
        endpoint = f"/authz/shares/entities/{stream_grn}"

        # 2. Construire le GRN du bénéficiaire (l'utilisateur)
        user_grn = f"grn::::user:{username}"

        # 3. Préparer le payload de la requête POST
        payload = {
            "selected_grantee_capability": role,
            "grantees": [user_grn]
        }

        # 4. Envoyer la requête POST
        # Une réponse réussie est souvent un 204 No Content, _make_request gère cela.
        # On ne s'attend pas à recevoir de JSON en retour, mais on vérifie si l'appel a réussi.
        response = self._make_request('POST', endpoint, data=payload)

        # _make_request retourne None en cas d'erreur HTTP.
        # Si la requête réussit (même avec un code 204), elle ne retourne pas None.
        if response is not None or (self.session.last_response and self.session.last_response.status_code == 204):
            print(f"✅ Succès ! Utilisateur '{username}' a maintenant le rôle '{role}' sur le stream '{stream_id}'.")
            return True
        else:
            print(f"❌ Échec de l'assignation.")
            return False

# Petite modification dans _make_request pour stocker la dernière réponse
# Ceci est utile pour vérifier les codes de statut comme 204 qui n'ont pas de corps JSON.
# Remplacez votre méthode _make_request par celle-ci :

    def _make_request(self, method, endpoint, params=None, data=None):
        """Méthode privée pour effectuer des requêtes et gérer les erreurs."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, params=params, json=data)
            self.session.last_response = response # Stocker la dernière réponse
            response.raise_for_status()
            
            # Gérer le cas où la réponse est vide (ex: 204 No Content)
            if response.status_code == 204:
                return {} # Retourner un dict vide pour signifier le succès
            
            return response.json()
        except requests.exceptions.HTTPError as e:
            print(f"Erreur HTTP: {e.response.status_code} pour l'URL {url}")
            print(f"Réponse: {e.response.text}")
        except requests.exceptions.ConnectionError as e:
            print(f"Erreur de connexion à l'URL {url}: {e}")
        except requests.exceptions.RequestException as e:
            print(f"Une erreur est survenue: {e}")
        return None



# ... (le reste du fichier, y compris la classe et la fonction main) ...

def demonstrate_permission_grant(api_client):
    """
    Fonction pour démontrer l'assignation de permission.
    ATTENTION : MODIFIE LES DONNÉES SUR VOTRE SERVEUR GRAYLOG.
    """
    print("\n" + "#"*50)
    print("### DÉMONSTRATION DE L'ASSIGNATION DE PERMISSION ###")
    print("#"*50)

    # --- À MODIFIER AVEC VOS PROPRES VALEURS ---
    target_username = "jdupont"  # L'utilisateur à qui donner la permission
    # Remplacez par un ID de stream de votre instance Graylog
    target_stream_id = "60a1c2d3e4f5a6b7c8d9e0f1" 
    target_role = "viewer"       # Le rôle à donner : 'viewer', 'manager'
    # --------------------------------------------

    # Vérification simple que les valeurs ont été changées
    if "jdupont" in target_username or "60a1c2d3e4f5a6b7c8d9e0f1" in target_stream_id:
        print("\n⚠️ ATTENTION: Veuillez modifier les variables `target_username` et `target_stream_id`")
        print("dans la fonction `demonstrate_permission_grant` avec des valeurs réelles de votre système.")
        return

    # Appel de la nouvelle méthode
    api_client.grant_user_to_stream(
        username=target_username,
        stream_id=target_stream_id,
        role=target_role
    )


if __name__ == "__main__":
    api = GraylogAPI()
    
    # 1. On liste l'état actuel
    main()

    # 2. On exécute l'action d'écriture
    # !! DÉCOMMENTEZ LA LIGNE CI-DESSOUS POUR TESTER L'ASSIGNATION !!
    # demonstrate_permission_grant(api)

    # 3. Optionnel : Relister les permissions pour voir le changement
    # print("\n\n--- Vérification des permissions après l'assignation ---")
    # main()




import requests
import configparser
import json
import sys
from collections import defaultdict

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

        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Requested-By': 'PythonGraylogClient'
        })
        self.session.auth = (self.token, 'token')

    def _make_request(self, method, endpoint, params=None, data=None):
        """Méthode privée pour effectuer des requêtes et gérer les erreurs."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, params=params, json=data)
            response.raise_for_status()
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
        """Récupère toutes les pages d'un résultat paginé."""
        all_items = []
        page = 1
        per_page = 50
        
        while True:
            params = {'page': page, 'per_page': per_page}
            data = self._make_request('GET', endpoint, params=params)
            
            if not data or not data.get(key_name):
                break
                
            items = data[key_name]
            all_items.extend(items)
            
            if len(items) < per_page:
                break
                
            page += 1
            
        return all_items

    def get_streams(self):
        """Récupère la liste de tous les streams."""
        print("Récupération de la liste des streams...")
        data = self._make_request('GET', '/streams')
        return data.get('streams', []) if data else []

    def get_users(self):
        """Récupère la liste de tous les utilisateurs."""
        print("Récupération de la liste des utilisateurs...")
        return self._get_paginated_results('/users', 'users')

    # --- NOUVELLE MÉTHODE ---
    def get_all_permissions(self):
        """
        Récupère un aperçu de toutes les permissions ("grants") dans le système.
        """
        print("Récupération de toutes les permissions du système...")
        # Cet endpoint peut être paginé sur les grosses instances de Graylog
        return self._get_paginated_results('/authz/grants-overview', 'grants')


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
        
    print("\n" + "="*50)
    print(f"Total des utilisateurs trouvés : {len(users)}")
    print("="*50)
    for user in users:
        print(f"- Username: {user['username']} | Full Name: {user['full_name']} | Email: {user['email']}")

    # --- 3. Lister les permissions par stream (LOGIQUE CORRIGÉE) ---
    all_permissions = api.get_all_permissions()
    
    # On pré-traite les permissions pour les organiser par stream
    stream_permissions = defaultdict(list)
    if all_permissions:
        for perm in all_permissions:
            target_grn = perm.get('target')
            # On ne garde que les permissions qui concernent un stream
            if target_grn and target_grn.startswith('grn::::stream:'):
                # On extrait l'ID du stream depuis le GRN
                stream_id = target_grn.split(':')[-1]
                stream_permissions[stream_id].append(perm)

    print("\n" + "="*50)
    print("Permissions sur les Streams")
    print("="*50)
    for stream in streams:
        print(f"\n--- Stream : '{stream['title']}' (ID: {stream['id']}) ---")
        # On récupère les permissions pré-traitées pour ce stream
        permissions_for_this_stream = stream_permissions.get(stream['id'])
        
        if permissions_for_this_stream:
            for perm in permissions_for_this_stream:
                grantee = perm['grantee']
                capability = perm['capability']
                print(f"  -> Rôle '{capability}' accordé à '{grantee}'")
        else:
            # On vérifie si l'utilisateur qui a créé le stream a la permission "owner"
            # qui n'apparaît pas toujours dans grants-overview.
            owner_user_id = stream.get('creator_user_id')
            if owner_user_id:
                 print(f"  -> Rôle 'Owner' (implicite) accordé à l'utilisateur créateur (ID: {owner_user_id})")
            else:
                print("  -> Aucune permission explicite trouvée pour ce stream.")


if __name__ == "__main__":
    main()






































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

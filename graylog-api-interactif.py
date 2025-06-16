import requests
import sys
import os
import configparser

class GraylogAPI:
    """
    Classe pour interagir avec l'API de Graylog.
    Gère l'authentification et les appels API de manière robuste.
    """
    def __init__(self, base_url, token):
        """Initialise le client API avec l'URL et le token."""
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Requested-By': 'PythonInteractiveClient'
        })
        # L'authentification se fait avec le token comme nom d'utilisateur et "token" comme mot de passe.
        self.session.auth = (token, 'token')
        self.last_response = None # Pour stocker la dernière réponse pour le débogage

    def _make_request(self, method, endpoint, params=None, data=None):
        """Méthode privée pour effectuer les requêtes et gérer les erreurs communes."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, params=params, json=data)
            self.last_response = response
            response.raise_for_status() # Lève une exception pour les erreurs 4xx/5xx

            # Pour les requêtes réussies qui ne retournent pas de contenu (ex: 204 No Content)
            if response.status_code in [200, 201, 204]:
                return response.json() if response.content else {}
            
            return response.json()
        except requests.exceptions.HTTPError as e:
            print(f"\n❌ Erreur HTTP: {e.response.status_code} pour l'URL {url}")
            print(f"   Réponse de l'API: {e.response.text}")
        except requests.exceptions.ConnectionError as e:
            print(f"\n❌ Erreur de connexion à l'URL {url}: {e}")
        except requests.exceptions.RequestException as e:
            print(f"\n❌ Une erreur inattendue est survenue: {e}")
        return None

    def _get_paginated_results(self, endpoint, key_name):
        """Récupère toutes les pages d'un résultat paginé."""
        all_items = []
        page = 1
        per_page = 50 # Taille de page standard
        while True:
            params = {'page': page, 'per_page': per_page}
            data = self._make_request('GET', endpoint, params=params)
            if data is None: break
            if not data.get(key_name): break
            items = data[key_name]
            all_items.extend(items)
            if len(items) < per_page: break
            page += 1
        return all_items

    def get_streams(self):
        """Récupère la liste de tous les streams."""
        data = self._make_request('GET', '/streams')
        return data.get('streams', []) if data else None

    def get_users(self):
        """Récupère la liste de tous les utilisateurs."""
        return self._get_paginated_results('/users', 'users')

    def get_specific_stream_permissions(self, stream_id):
        """Récupère les permissions pour un seul stream."""
        stream_grn = f"grn::::stream:{stream_id}"
        endpoint = f"/authz/shares/entities/{stream_grn}"
        data = self._make_request('GET', endpoint)
        return data.get('grants', []) if data else []

    def grant_user_to_stream(self, username, stream_id, role):
        """
        Assigne un utilisateur à un stream avec un rôle, en préservant les permissions existantes.
        """
        print("\n1. Récupération des permissions actuelles du stream...")
        current_permissions = self.get_specific_stream_permissions(stream_id)
        if current_permissions is None:
            print("❌ Impossible de récupérer les permissions actuelles. Abandon.")
            return False

        # 2. Construction du nouvel objet de permissions à partir des permissions existantes
        new_permissions_payload = {}
        for perm in current_permissions:
            new_permissions_payload[perm['grantee']] = perm['capability']
        
        print(f"   Permissions existantes trouvées: {len(new_permissions_payload)}")
        
        # 3. Ajout ou modification de la permission pour le nouvel utilisateur
        user_grn = f"grn::::user:{username}"
        print(f"2. Ajout/Mise à jour de la permission '{role}' pour l'utilisateur '{username}' (GRN: {user_grn})")
        new_permissions_payload[user_grn] = role
        
        # 4. Construction du payload final pour la requête POST
        stream_grn = f"grn::::stream:{stream_id}"
        endpoint = f"/authz/shares/entities/{stream_grn}"
        final_payload = {
            "selected_grantee_capabilities": new_permissions_payload
        }
        
        print(f"3. Envoi du payload de mise à jour complet à l'API...")
        
        # 5. Envoi de la requête
        response = self._make_request('POST', endpoint, data=final_payload)
        
        return response is not None

def clear_screen():
    """Efface l'écran du terminal pour une meilleure lisibilité."""
    os.system('cls' if os.name == 'nt' else 'clear')

def select_from_list(items, title, display_key, return_key):
    """Affiche une liste d'items et demande à l'utilisateur d'en choisir un."""
    print(f"\n--- {title} ---")
    if not items:
        print("La liste est vide.")
        return None

    for i, item in enumerate(items):
        display_value = item.get(display_key)
        if display_key == 'username' and 'full_name' in item and item['full_name']:
            display_value = f"{item['full_name']} ({item['username']})"
        print(f"  {i+1}: {display_value} (ID: {item.get('id', 'N/A')})")
    
    while True:
        try:
            choice = input(f"\n> Veuillez choisir un {title.lower().split(' ')[0]} (entrez le numéro, ou 'q' pour quitter) : ")
            if choice.lower() == 'q':
                return None
            choice_index = int(choice) - 1
            if 0 <= choice_index < len(items):
                return items[choice_index][return_key]
            else:
                print("Numéro invalide, veuillez réessayer.")
        except ValueError:
            print("Entrée invalide, veuillez entrer un numéro.")
        except KeyboardInterrupt:
            return None

def main():
    """Fonction principale interactive."""
    clear_screen()
    print("="*50)
    print("=== Outil d'Assignation de Permissions Graylog ===")
    print("="*50)

    config_file = 'config.ini'
    config = configparser.ConfigParser()
    if not os.path.exists(config_file):
        print(f"❌ Erreur: Le fichier de configuration '{config_file}' n'a pas été trouvé.")
        sys.exit(1)
        
    config.read(config_file)
    
    try:
        graylog_url = config['graylog']['url']
        graylog_token = config['graylog']['token']
        if not graylog_url or not graylog_token or "VOTRE_TOKEN_API_SECRET_ICI" in graylog_token:
             raise KeyError
    except KeyError:
        print(f"❌ Erreur: Le fichier '{config_file}' doit contenir les clés 'url' et 'token' dans la section [graylog].")
        print("Veuillez vérifier que le fichier est correctement rempli.")
        sys.exit(1)

    print(f"🔧 Connexion à l'instance Graylog : {graylog_url}")
    api = GraylogAPI(graylog_url, graylog_token)

    print("\n🔄 Récupération des données initiales (streams, utilisateurs)...")
    streams = api.get_streams()
    users = api.get_users()

    if streams is None or users is None:
        print("\nImpossible de récupérer les données de base. Vérifiez l'URL et le token dans votre config.ini.")
        sys.exit(1)

    clear_screen()
    
    while True:
        selected_stream_id = select_from_list(streams, "Liste des Streams", 'title', 'id')
        if not selected_stream_id: break
        
        selected_username = select_from_list(users, "Liste des Utilisateurs", 'username', 'username')
        if not selected_username: break

        roles = ['viewer', 'manager']
        selected_role = select_from_list(
            [{'role': r} for r in roles], 
            "Rôle à assigner", 
            'role', 
            'role'
        )
        if not selected_role: break

        print("\n--- RÉCAPITULATIF ---")
        print(f"  Stream   : {selected_stream_id}")
        print(f"  Utilisateur: {selected_username}")
        print(f"  Rôle     : {selected_role}")
        
        confirm = input("\n> Confirmez-vous cette assignation ? (o/N) : ").lower()
        if confirm == 'o':
            print("\n🚀 Processus d'assignation...")
            success = api.grant_user_to_stream(selected_username, selected_stream_id, selected_role)
            if success:
                print(f"\n✅ Succès ! Les permissions du stream ont été mises à jour.")
            else:
                print("\n❌ Échec de l'assignation. Veuillez vérifier les logs d'erreur ci-dessus.")
        else:
            print("Opération annulée.")
        
        another = input("\nVoulez-vous effectuer une autre assignation ? (o/N) : ").lower()
        if another != 'o':
            break
        clear_screen()

    print("\nFin du script. Au revoir !")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nOpération interrompue par l'utilisateur. Au revoir !")
        sys.exit(0)

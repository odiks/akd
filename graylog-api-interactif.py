import requests
import sys
import os
import configparser # Importation pour lire le fichier .ini

# --- Suppression des constantes, car elles seront dans config.ini ---

class GraylogAPI:
    """Classe pour interagir avec l'API de Graylog."""
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Requested-By': 'PythonInteractiveClient'
        })
        self.session.auth = (token, 'token')
        self.last_response = None

    def _make_request(self, method, endpoint, params=None, data=None):
        """Méthode privée pour effectuer des requêtes et gérer les erreurs."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, params=params, json=data)
            self.last_response = response
            response.raise_for_status()
            if response.status_code == 204:
                return {}
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
        per_page = 50
        while True:
            params = {'page': page, 'per_page': per_page}
            data = self._make_request('GET', endpoint, params=params)
            if not data or not data.get(key_name): break
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

    def grant_user_to_stream(self, username, stream_id, role):
        """Assigne un utilisateur à un stream avec un rôle spécifique."""
        stream_grn = f"grn::::stream:{stream_id}"
        user_grn = f"grn::::user:{username}"
        endpoint = f"/authz/shares/entities/{stream_grn}"
        payload = {
            "selected_grantee_capability": role,
            "grantees": [user_grn]
        }
        response = self._make_request('POST', endpoint, data=payload)
        return response is not None


def clear_screen():
    """Efface l'écran du terminal."""
    os.system('cls' if os.name == 'nt' else 'clear')

def select_from_list(items, title, display_key, return_key):
    """Affiche une liste et demande à l'utilisateur de choisir un item."""
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

    # --- Lecture de la configuration ---
    config_file = 'config.ini'
    config = configparser.ConfigParser()
    if not os.path.exists(config_file):
        print(f"❌ Erreur: Le fichier de configuration '{config_file}' n'a pas été trouvé.")
        print("Veuillez créer ce fichier avec une section [graylog] contenant 'url' et 'token'.")
        sys.exit(1)
        
    config.read(config_file)
    
    try:
        graylog_url = config['graylog']['url']
        graylog_token = config['graylog']['token']
        if not graylog_url or not graylog_token or "VOTRE" in graylog_token:
             raise KeyError
    except KeyError:
        print(f"❌ Erreur: Le fichier '{config_file}' doit contenir les clés 'url' et 'token' dans la section [graylog].")
        print("Veuillez vérifier que le fichier est correctement rempli.")
        sys.exit(1)

    print(f"🔧 Connexion à l'instance Graylog : {graylog_url}")
    api = GraylogAPI(graylog_url, graylog_token)

    # --- Récupération des données ---
    print("\n🔄 Récupération des données depuis Graylog...")
    streams = api.get_streams()
    users = api.get_users()

    if streams is None or users is None:
        print("\nImpossible de récupérer les données de base (streams/utilisateurs).")
        print("Veuillez vérifier l'URL et le token dans votre fichier config.ini.")
        sys.exit(1)

    clear_screen()
    
    # --- Boucle interactive ---
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
            print("\n🚀 Assignation en cours...")
            success = api.grant_user_to_stream(selected_username, selected_stream_id, selected_role)
            if success:
                print(f"\n✅ Succès ! L'utilisateur '{selected_username}' a bien été assigné au stream avec le rôle '{selected_role}'.")
            else:
                print("\n❌ Échec de l'assignation. Veuillez vérifier les logs d'erreur ci-dessus.")
        else:
            print("Opération annulée.")
        
        # Demander si l'utilisateur veut continuer
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

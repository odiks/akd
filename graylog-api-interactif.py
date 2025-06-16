import requests
import sys
import getpass # Pour la saisie sécurisée du token
import os # Pour nettoyer l'écran

# --- Configuration ---
# L'URL de base de votre API Graylog. Modifiez-la si nécessaire.
GRAYLOG_URL = "https://votre-serveur-graylog.com/api"

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
        # L'authentification se fait avec le token comme nom d'utilisateur et "token" comme mot de passe.
        self.session.auth = (token, 'token')
        self.last_response = None # Pour stocker la dernière réponse

    def _make_request(self, method, endpoint, params=None, data=None):
        """Méthode privée pour effectuer des requêtes et gérer les erreurs."""
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, params=params, json=data)
            self.last_response = response
            response.raise_for_status()
            if response.status_code == 204: # 204 No Content
                return {} # Succès mais pas de contenu JSON
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
        user_grn = f"grn::::user:{username}" # L'API utilise le username pour le GRN
        endpoint = f"/authz/shares/entities/{stream_grn}"
        payload = {
            "selected_grantee_capability": role,
            "grantees": [user_grn]
        }
        response = self._make_request('POST', endpoint, data=payload)
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
        # Affiche le nom complet pour les utilisateurs si disponible, sinon le username
        display_value = item.get(display_key)
        if display_key == 'username' and 'full_name' in item and item['full_name']:
            display_value = f"{item['full_name']} ({item['username']})"
        print(f"  {i+1}: {display_value} (ID: {item.get('id', 'N/A')})")
    
    while True:
        try:
            choice = input(f"\n> Veuillez choisir un {title.lower().split(' ')[0]} (entrez le numéro) : ")
            choice_index = int(choice) - 1
            if 0 <= choice_index < len(items):
                return items[choice_index][return_key]
            else:
                print("Numéro invalide, veuillez réessayer.")
        except ValueError:
            print("Entrée invalide, veuillez entrer un numéro.")
        except KeyboardInterrupt:
            print("\nOpération annulée.")
            return None


def main():
    """Fonction principale interactive."""
    clear_screen()
    print("="*50)
    print("=== Outil d'Assignation de Permissions Graylog ===")
    print("="*50)

    # --- Connexion ---
    graylog_url_input = input(f"Entrez l'URL de l'API Graylog ou appuyez sur Entrée pour utiliser [{GRAYLOG_URL}]: ")
    if not graylog_url_input:
        graylog_url_input = GRAYLOG_URL
        
    try:
        graylog_token = getpass.getpass("Entrez votre Token d'API Graylog : ")
        if not graylog_token:
            print("Le token ne peut pas être vide.")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\nOpération annulée.")
        sys.exit(1)

    api = GraylogAPI(graylog_url_input, graylog_token)

    # --- Récupération des données ---
    print("\n🔄 Récupération des données depuis Graylog...")
    streams = api.get_streams()
    users = api.get_users()

    if streams is None or users is None:
        print("\nImpossible de récupérer les données de base (streams/utilisateurs). Vérifiez votre URL et votre token.")
        sys.exit(1)

    clear_screen()
    
    # --- Sélection interactive ---
    selected_stream_id = select_from_list(streams, "Liste des Streams", 'title', 'id')
    if not selected_stream_id: sys.exit(1)
    
    # L'API a besoin du USERNAME pour construire le GRN, pas de l'ID numérique de l'utilisateur.
    selected_username = select_from_list(users, "Liste des Utilisateurs", 'username', 'username')
    if not selected_username: sys.exit(1)

    # --- Sélection du rôle ---
    roles = ['viewer', 'manager']
    selected_role = select_from_list(
        [{'role': r} for r in roles], 
        "Rôle à assigner", 
        'role', 
        'role'
    )
    if not selected_role: sys.exit(1)

    # --- Confirmation et Action ---
    print("\n--- RÉCAPITULATIF ---")
    print(f"  Stream   : {selected_stream_id}")
    print(f"  Utilisateur: {selected_username}")
    print(f"  Rôle     : {selected_role}")
    
    confirm = input("\n> Confirmez-vous cette assignation ? (o/N) : ").lower()
    if confirm != 'o':
        print("Opération annulée.")
        sys.exit(0)

    print("\n🚀 Assignation en cours...")
    success = api.grant_user_to_stream(selected_username, selected_stream_id, selected_role)

    if success:
        print(f"\n✅ Succès ! L'utilisateur '{selected_username}' a bien été assigné au stream avec le rôle '{selected_role}'.")
    else:
        print("\n❌ Échec de l'assignation. Veuillez vérifier les logs d'erreur ci-dessus.")


if __name__ == "__main__":
    main()

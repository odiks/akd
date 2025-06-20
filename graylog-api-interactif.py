import requests
import sys
import os
import configparser
import json

class GraylogAPI:
    """
    Classe pour interagir avec l'API de Graylog.
    Gère l'authentification et les appels API de manière robuste.
    """
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
        url = f"{self.base_url}{endpoint}"
        try:
            response = self.session.request(method, url, params=params, json=data)
            self.last_response = response
            response.raise_for_status()
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
        all_items = []
        page = 1
        per_page = 50
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
        data = self._make_request('GET', '/streams')
        return data.get('streams', []) if data else None

    def get_users(self):
        return self._get_paginated_results('/users', 'users')

    def get_specific_stream_permissions(self, stream_id):
        stream_grn = f"grn::::stream:{stream_id}"
        endpoint = f"/authz/shares/entities/{stream_grn}"
        data = self._make_request('GET', endpoint)
        return data.get('grants', []) if data else []

    def grant_user_to_stream(self, user_id_to_add, stream_id, role, owner_user_id):
        print("\n1. Récupération des permissions actuelles du stream...")
        current_permissions = self.get_specific_stream_permissions(stream_id)
        if current_permissions is None:
            return False

        new_permissions_payload = {}
        for perm in current_permissions:
            new_permissions_payload[perm['grantee']] = perm['capability']
        
        print(f"   Permissions existantes trouvées: {len(new_permissions_payload)}")
        
        print("2. Vérification et application du rôle 'own' pour le propriétaire du stream...")
        owner_grn = f"grn::::user:{owner_user_id}"
        new_permissions_payload[owner_grn] = "own"
        
        user_to_add_grn = f"grn::::user:{user_id_to_add}"
        if user_id_to_add != owner_user_id:
            print(f"3. Ajout/Mise à jour de la permission '{role}' pour l'utilisateur ID '{user_id_to_add}'")
            new_permissions_payload[user_to_add_grn] = role
        else:
            print(f"3. L'utilisateur sélectionné est déjà le propriétaire. Son rôle 'own' est garanti.")

        stream_grn = f"grn::::stream:{stream_id}"
        endpoint = f"/authz/shares/entities/{stream_grn}"
        final_payload = { "selected_grantee_capabilities": new_permissions_payload }
        
        print(f"4. Envoi du payload de mise à jour complet à l'API...")

        full_url = f"{self.base_url}{endpoint}"
        headers_str = " ".join([f"-H '{k}: {v}'" for k, v in self.session.headers.items()])
        auth_str = f"--user '{self.session.auth[0]}:{self.session.auth[1]}'"
        data_str = f"--data-raw '{json.dumps(final_payload)}'"
        curl_command = f"curl -X POST {auth_str} {headers_str} '{full_url}' {data_str}"
        
        print("\n" + "="*70)
        print("ÉQUIVALENT DE LA REQUÊTE EN COMMANDE cURL :")
        print(curl_command)
        print("="*70 + "\n")
        
        response = self._make_request('POST', endpoint, data=final_payload)
        
        return response is not None

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def select_from_list(items, title, display_key):
    print(f"\n--- {title} ---")
    if not items: return None
    for i, item in enumerate(items):
        display_value = item.get(display_key)
        if display_key == 'username' and 'full_name' in item and item['full_name']:
            display_value = f"{item['full_name']} ({item['username']})"
        print(f"  {i+1}: {display_value} (ID: {item.get('id', 'N/A')})")
    while True:
        try:
            choice = input(f"\n> Veuillez choisir un {title.lower().split(' ')[0]} (entrez le numéro, ou 'q' pour quitter) : ")
            if choice.lower() == 'q': return None
            choice_index = int(choice) - 1
            if 0 <= choice_index < len(items):
                return items[choice_index]
            else:
                print("Numéro invalide.")
        except (ValueError, KeyboardInterrupt):
            return None

def main():
    clear_screen()
    print("="*60)
    print("=== Outil d'Assignation de Permissions Graylog (Final) ===")
    print("="*60)

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
        print(f"❌ Erreur: Le fichier '{config_file}' doit contenir les clés 'url' et 'token'.")
        sys.exit(1)

    print(f"🔧 Connexion à l'instance Graylog : {graylog_url}")
    api = GraylogAPI(graylog_url, graylog_token)

    print("\n🔄 Récupération des données initiales...")
    streams = api.get_streams()
    users = api.get_users()

    if streams is None or users is None:
        print("\nImpossible de récupérer les données de base.")
        sys.exit(1)

    # --- NOUVEAU : Création des mappings pour faciliter les recherches ---
    user_id_to_username_map = {user['id']: user['username'] for user in users}
    
    ROLE_MAPPING = { 'Viewer': 'view', 'Manager': 'manage' }
    
    clear_screen()
    
    while True:
        # --- Étape 1 : Sélection du Stream ---
        selected_stream_obj = select_from_list(streams, "Liste des Streams", 'title')
        if not selected_stream_obj: break
        selected_stream_id = selected_stream_obj['id']
        
        # Récupération de l'ID du créateur depuis l'objet stream
        creator_id_from_stream = selected_stream_obj.get('creator_user_id')
        if not creator_id_from_stream:
            print(f"❌ Erreur: ID du propriétaire introuvable pour le stream '{selected_stream_id}'.")
            continue

        # --- Étape 2 : Identification et Confirmation du Propriétaire ---
        print("\n" + "-"*50)
        print("--- ÉTAPE DE VÉRIFICATION DU PROPRIÉTAIRE ---")
        
        # On trouve le username du propriétaire grâce à son ID
        owner_username = user_id_to_username_map.get(creator_id_from_stream)
        
        if not owner_username:
            print(f"⚠️ Avertissement: Le propriétaire avec l'ID '{creator_id_from_stream}' n'a pas été trouvé dans la liste des utilisateurs actifs.")
            print("   Il s'agit peut-être d'un utilisateur supprimé. La requête risque d'échouer.")
            # On utilise l'ID original comme solution de repli
            final_owner_id = creator_id_from_stream
        else:
            # On utilise l'ID qui correspond au nom d'utilisateur trouvé.
            # Dans la plupart des cas, ce sera identique à creator_id_from_stream,
            # mais cela respecte la logique de "mapper" le nom à un ID.
            final_owner_id = creator_id_from_stream # Gardons la source de vérité de l'API stream

        print(f"Le propriétaire détecté pour ce stream est '{owner_username or 'Utilisateur Inconnu'}'.")
        print(f"L'ID suivant sera utilisé pour le rôle 'own' : {final_owner_id}")
        
        confirm_owner = input("> Confirmez-vous cet utilisateur comme propriétaire ? (o/N) : ").lower()
        if confirm_owner != 'o':
            print("Opération annulée. Retour à la sélection du stream.")
            clear_screen()
            continue
        
        # --- Étape 3 : Sélection de l'utilisateur à ajouter ---
        selected_user_obj = select_from_list(users, "Liste des Utilisateurs", 'username')
        if not selected_user_obj: break
        user_id_to_add = selected_user_obj['id']
        username_for_display = selected_user_obj['username']
        
        # --- Étape 4 : Sélection du rôle ---
        display_roles = list(ROLE_MAPPING.keys())
        roles_obj = [{'display_name': r} for r in display_roles]
        selected_role_obj = select_from_list(roles_obj, "Rôle à assigner", 'display_name')
        if not selected_role_obj: break
        
        selected_display_name = selected_role_obj['display_name']
        api_role_value = ROLE_MAPPING[selected_display_name]

        # --- Étape 5 : Récapitulatif et Confirmation Finale ---
        print("\n--- RÉCAPITULATIF FINAL ---")
        print(f"  Stream       : {selected_stream_obj['title']} (ID: {selected_stream_id})")
        print(f"  Utilisateur  : {username_for_display} (ID: {user_id_to_add})")
        print(f"  Rôle         : {selected_display_name} (valeur API: '{api_role_value}')")
        print(f"  Propriétaire (confirmé) : Utilisateur ID {final_owner_id} (rôle API: 'own')")
        
        confirm = input("\n> Confirmez-vous l'envoi de cette requête ? (o/N) : ").lower()
        if confirm == 'o':
            print("\n🚀 Processus d'assignation...")
            success = api.grant_user_to_stream(user_id_to_add, selected_stream_id, api_role_value, final_owner_id)
            if success:
                print(f"✅ Succès ! Les permissions du stream ont été mises à jour.")
            else:
                print(f"❌ Échec de l'assignation.")
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

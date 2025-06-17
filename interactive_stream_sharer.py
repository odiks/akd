#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import os
import sys
import json

# --- Configuration ---
GRAYLOG_BASE_URL = os.getenv("GRAYLOG_URL", "http://your-graylog-instance:9000")
API_TOKEN = os.getenv("GRAYLOG_API_TOKEN")

# ==============================================================================
# SECTION 1: FONCTIONS D'INTERACTION AVEC L'API GRAYLOG (Fusionnées)
# ==============================================================================

def _make_api_request(method: str, endpoint: str, payload: dict = None) -> dict | None:
    """
    Fonction d'aide générique pour les requêtes à l'API Graylog (GET, PUT, POST...).
    """
    if not API_TOKEN:
        print("Erreur: La variable d'environnement GRAYLOG_API_TOKEN n'est pas définie.", file=sys.stderr)
        return None

    api_url = f"{GRAYLOG_BASE_URL}{endpoint}"
    headers = {'Accept': 'application/json', 'Content-Type': 'application/json', 'X-Requested-By': 'interactive-stream-sharer'}

    try:
        response = requests.request(
            method,
            api_url,
            headers=headers,
            auth=(API_TOKEN, 'token'),
            json=payload if payload else None
        )
        response.raise_for_status()
        # PUT peut retourner 204 No Content, qui n'a pas de corps JSON
        return response.json() if response.status_code != 204 else {}
    except requests.exceptions.HTTPError as http_err:
        print(f"❌ Erreur HTTP ({http_err.response.status_code}) en interrogeant '{api_url}':", file=sys.stderr)
        print(f"   Réponse de l'API: {http_err.response.text}", file=sys.stderr)
    except requests.exceptions.RequestException as err:
        print(f"❌ Erreur de connexion à '{api_url}': {err}", file=sys.stderr)
    return None

def get_all_streams() -> list[dict] | None:
    """Récupère la liste de tous les streams."""
    print("⏳ Récupération de la liste des streams...")
    response_data = _make_api_request("GET", "/api/streams")
    if response_data and 'streams' in response_data:
        streams_list = [
            {"stream_name": s.get('title'), "stream_id": s.get('id'), "creator_user_id": s.get('creator_user_id')}
            for s in response_data['streams']
        ]
        print(f"✅ {len(streams_list)} streams trouvés.")
        return streams_list
    return None

def get_all_users() -> list[dict] | None:
    """Récupère la liste de tous les utilisateurs."""
    print("⏳ Récupération de la liste des utilisateurs...")
    response_data = _make_api_request("GET", "/api/users")
    if response_data and 'users' in response_data:
        users_list = [
            {"username": u.get('username'), "user_id": u.get('id')}
            for u in response_data['users']
        ]
        print(f"✅ {len(users_list)} utilisateurs trouvés.")
        return users_list
    return None

def add_users_to_stream(stream_id: str, users_to_add: list[dict]) -> bool:
    """
    Ajoute des utilisateurs à un stream de manière sûre (en préservant les permissions existantes).
    """
    entity_grn = f"grn::::stream:{stream_id}"
    api_url = f"/api/authz/shares/entities/{entity_grn}"

    # 1. LIRE les permissions existantes
    print("\n1. Lecture des permissions existantes...")
    current_permissions = _make_api_request("GET", api_url)
    if current_permissions is None:
        return False
    
    capabilities = current_permissions.get("selected_grantee_capabilities", {})
    
    # 2. MODIFIER en ajoutant les nouveaux utilisateurs
    print("2. Préparation de la mise à jour...")
    for user in users_to_add:
        user_grn = f"grn::::user:{user['id']}"
        capabilities[user_grn] = user['role']
        print(f"   + Ajout de '{user['id']}' avec le rôle '{user['role']}'.")
        
    # 3. ÉCRIRE les nouvelles permissions
    print("3. Envoi des permissions mises à jour...")
    result = _make_api_request("PUT", api_url, payload={"selected_grantee_capabilities": capabilities})
    
    if result is not None:
        print(f"\n✅ Succès ! Les permissions du stream '{stream_id}' ont été mises à jour.")
        return True
    return False

# ==============================================================================
# SECTION 2: LOGIQUE INTERACTIVE
# ==============================================================================

def select_from_list(items: list, display_key: str, id_key: str, prompt: str) -> dict | None:
    """Fonction d'aide pour faire un choix dans une liste numérotée."""
    print(f"\n--- {prompt} ---")
    for i, item in enumerate(items):
        print(f"  [{i+1}] {item[display_key]} (ID: {item[id_key]})")
    
    while True:
        try:
            choice = input(f"\nEntrez le numéro de votre choix (1-{len(items)}) : ")
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(items):
                return items[choice_idx]
            else:
                print("Numéro invalide. Veuillez réessayer.")
        except (ValueError, TypeError):
            print("Entrée non valide. Veuillez entrer un numéro.")

# --- Point d'entrée principal ---
if __name__ == "__main__":
    # Vérifications initiales
    if "your-graylog-instance" in GRAYLOG_BASE_URL or not API_TOKEN:
        print("Erreur: Veuillez configurer les variables d'environnement GRAYLOG_URL et GRAYLOG_API_TOKEN.", file=sys.stderr)
        sys.exit(1)

    # ÉTAPE 1: RÉCUPÉRER TOUTES LES DONNÉES
    all_streams = get_all_streams()
    all_users = get_all_users()
    if not all_streams or not all_users:
        print("\nImpossible de continuer sans les données des streams et des utilisateurs.", file=sys.stderr)
        sys.exit(1)

    # ÉTAPE 2: SÉLECTIONNER LE STREAM
    selected_stream = select_from_list(all_streams, "stream_name", "stream_id", "Sélectionnez le stream à modifier")
    if not selected_stream:
        sys.exit(1)
        
    stream_id = selected_stream['stream_id']
    owner_id = selected_stream['creator_user_id'] # Le créateur est considéré comme le propriétaire
    print(f"\nStream sélectionné : '{selected_stream['stream_name']}'")
    print(f"  ID du Stream : {stream_id}")
    print(f"  Propriétaire (créateur) : {owner_id}")

    # ÉTAPE 3: DEMANDER COMBIEN D'UTILISATEURS AJOUTER
    num_to_add = 0
    while True:
        try:
            num_str = input("\nCombien d'utilisateurs souhaitez-vous ajouter ? ")
            num_to_add = int(num_str)
            if num_to_add > 0:
                break
            print("Veuillez entrer un nombre supérieur à 0.")
        except ValueError:
            print("Entrée invalide. Veuillez entrer un nombre.")

    # ÉTAPE 4: SÉLECTIONNER LES UTILISATEURS À AJOUTER
    users_to_add = []
    available_users = [u for u in all_users if u['user_id'] != owner_id] # On ne peut pas s'ajouter soi-même
    
    while len(users_to_add) < num_to_add:
        print(f"\n--- Sélection de l'utilisateur {len(users_to_add) + 1}/{num_to_add} ---")
        
        # Créer une liste d'utilisateurs encore disponibles
        selectable_users = [u for u in available_users if u['user_id'] not in [sel['id'] for sel in users_to_add]]
        if not selectable_users:
            print("Il n'y a plus d'utilisateurs disponibles à ajouter.")
            break
            
        # Demander à l'utilisateur de choisir
        user_choice = select_from_list(selectable_users, "username", "user_id", "Choisissez un utilisateur à ajouter")
        if user_choice:
            users_to_add.append({'id': user_choice['user_id'], 'role': 'view'}) # Rôle fixé à 'view'
            print(f"  -> Utilisateur '{user_choice['username']}' ajouté à la liste.")

    if not users_to_add:
        print("\nAucun utilisateur n'a été sélectionné. Opération annulée.")
        sys.exit(0)

    # ÉTAPE 5: CONFIRMATION FINALE ET EXÉCUTION
    print("\n--- RÉSUMÉ DE L'OPÉRATION ---")
    print(f"Stream : '{selected_stream['stream_name']}' (ID: {stream_id})")
    print("Utilisateurs à ajouter (avec le rôle 'view'):")
    for u in users_to_add:
        # Trouver le username pour un affichage plus clair
        username = next((item['username'] for item in all_users if item['user_id'] == u['id']), u['id'])
        print(f"  - {username}")
        
    confirm = input("\nConfirmez-vous cette opération ? (oui/non) : ").lower()
    
    if confirm in ['oui', 'o', 'yes', 'y']:
        add_users_to_stream(stream_id, users_to_add)
    else:
        print("\nOpération annulée par l'utilisateur.")

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import os
import sys
import argparse

# --- Configuration ---
GRAYLOG_BASE_URL = os.getenv("GRAYLOG_URL", "http://your-graylog-instance:9000")
API_TOKEN = os.getenv("GRAYLOG_API_TOKEN")

# --- Fonctions ---

def set_stream_permissions(stream_id: str, owner_user_id: str, users_to_set: list[dict]):
    """
    Écrase et remplace toutes les permissions d'un stream par une nouvelle liste.

    Cette fonction est DESTRUCTIVE. Toutes les permissions existantes seront supprimées
    et remplacées par celles fournies.

    Args:
        stream_id (str): L'ID du stream à modifier.
        owner_user_id (str): L'ID de l'utilisateur propriétaire du stream. C'est OBLIGATOIRE.
        users_to_set (list[dict]): La liste complète des utilisateurs (sauf le propriétaire)
                                   et de leurs rôles.
                                   Exemple: [{'id': 'user1', 'role': 'view'}]
    
    Returns:
        bool: True si l'opération a réussi, False sinon.
    """
    if not API_TOKEN:
        print("Erreur: La variable d'environnement GRAYLOG_API_TOKEN n'est pas définie.", file=sys.stderr)
        return False

    entity_grn = f"grn::::stream:{stream_id}"
    api_url = f"{GRAYLOG_BASE_URL}/api/authz/shares/entities/{entity_grn}"
    
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-Requested-By': 'python-script-overwrite-permissions'
    }
    auth = (API_TOKEN, 'token')

    # 1. Préparer le payload à partir de zéro
    print("1. Construction de la nouvelle liste de permissions...")
    capabilities_to_set = {}
    
    # Étape 1.1 : Définir le propriétaire (obligatoire)
    owner_grn = f"grn::::user:{owner_user_id}"
    capabilities_to_set[owner_grn] = "own"
    print(f"   - Propriétaire défini : '{owner_user_id}'")

    # Étape 1.2 : Ajouter les autres utilisateurs de la liste
    for user in users_to_set:
        user_id = user['id']
        user_role = user['role']
        user_grn = f"grn::::user:{user_id}"
        capabilities_to_set[user_grn] = user_role
        print(f"   - Utilisateur à ajouter : '{user_id}' avec le rôle '{user_role}'")

    final_payload = {"selected_grantee_capabilities": capabilities_to_set}

    try:
        # 2. ÉCRIRE : Envoyer le nouveau jeu de permissions.
        # Cette requête va écraser toutes les permissions précédentes.
        print(f"\n2. Envoi des nouvelles permissions pour le stream '{stream_id}'...")
        response = requests.put(api_url, headers=headers, json=final_payload, auth=auth)
        response.raise_for_status()

        print(f"\n✅ Succès ! Les permissions du stream '{stream_id}' ont été entièrement remplacées.")
        return True

    except requests.exceptions.HTTPError as http_err:
        print(f"❌ Erreur HTTP: {http_err}", file=sys.stderr)
        if http_err.response:
             print(f"   Réponse de l'API (code {http_err.response.status_code}): {http_err.response.text}", file=sys.stderr)
    except requests.exceptions.RequestException as err:
        print(f"❌ Erreur de requête: {err}", file=sys.stderr)
    
    return False

# --- Point d'entrée du script ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Écrase les permissions d'un stream Graylog avec une nouvelle liste d'utilisateurs.",
        epilog="ATTENTION : CETTE OPÉRATION EST DESTRUCTIVE ET REMPLACE TOUTES LES PERMISSIONS EXISTANTES.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("stream_id", help="L'ID du stream à modifier.")
    parser.add_argument("owner_id", help="L'ID de l'utilisateur propriétaire ('owner') du stream. Argument obligatoire.")
    parser.add_argument(
        "users", 
        nargs='*', # 0 ou plus utilisateurs. Permet de ne définir que le propriétaire si besoin.
        help="Liste des autres utilisateurs à ajouter au format USER_ID:ROLE.\nSi le rôle est omis, 'view' sera utilisé par défaut."
    )
    
    args = parser.parse_args()

    if "your-graylog-instance" in GRAYLOG_BASE_URL or not API_TOKEN:
        print("Erreur: Veuillez configurer les variables d'environnement GRAYLOG_URL et GRAYLOG_API_TOKEN.", file=sys.stderr)
        sys.exit(1)

    users_to_process = []
    for user_arg in args.users:
        if ':' in user_arg:
            user_id, role = user_arg.split(':', 1)
        else:
            user_id = user_arg
            role = "view"
        users_to_process.append({"id": user_id, "role": role})

    set_stream_permissions(args.stream_id, args.owner_id, users_to_process)

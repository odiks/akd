#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import os
import sys
import json

# --- Configuration ---
# Utilisation des variables d'environnement pour la sécurité et la flexibilité.
GRAYLOG_BASE_URL = os.getenv("GRAYLOG_URL", "http://your-graylog-instance:9000")
API_TOKEN = os.getenv("GRAYLOG_API_TOKEN")

# --- Fonctions ---

def _make_api_get_request(endpoint: str) -> dict | None:
    """
    Fonction d'aide pour effectuer une requête GET authentifiée à l'API Graylog.
    Centralise la gestion des erreurs et de l'authentification.

    Args:
        endpoint (str): Le chemin de l'API à interroger (ex: "/api/users").

    Returns:
        dict | None: Le corps de la réponse JSON en cas de succès, sinon None.
    """
    if not API_TOKEN:
        print("Erreur: La variable d'environnement GRAYLOG_API_TOKEN n'est pas définie.", file=sys.stderr)
        return None

    api_url = f"{GRAYLOG_BASE_URL}{endpoint}"
    headers = {
        'Accept': 'application/json',
        'X-Requested-By': 'python-script-get-info'
    }

    try:
        response = requests.get(
            api_url,
            headers=headers,
            auth=(API_TOKEN, 'token')
        )
        response.raise_for_status()  # Lève une exception pour les codes 4xx/5xx
        return response.json()

    except requests.exceptions.HTTPError as http_err:
        print(f"❌ Erreur HTTP en interrogeant '{api_url}': {http_err}", file=sys.stderr)
        print(f"   Réponse de l'API (code {http_err.response.status_code}): {http_err.response.text}", file=sys.stderr)
    except requests.exceptions.RequestException as err:
        print(f"❌ Erreur de connexion à '{api_url}': {err}", file=sys.stderr)
    
    return None

def get_all_streams() -> list[dict] | None:
    """
    Récupère la liste de tous les streams et extrait les informations pertinentes.

    Returns:
        list[dict] | None: Une liste de dictionnaires, chaque dictionnaire représentant un stream.
                            Retourne None en cas d'erreur.
    """
    print("⏳ Récupération de la liste des streams...")
    response_data = _make_api_get_request("/api/streams")
    
    if not response_data:
        return None

    streams_list = []
    # La réponse de l'API est un dictionnaire avec une clé 'streams' qui contient la liste
    for stream in response_data.get('streams', []):
        streams_list.append({
            "stream_name": stream.get('title'),
            "stream_id": stream.get('id'),
            "creator_user_id": stream.get('creator_user_id')
        })
    
    print(f"✅ {len(streams_list)} streams trouvés.")
    return streams_list

def get_all_users() -> list[dict] | None:
    """
    Récupère la liste de tous les utilisateurs et extrait les informations pertinentes.

    Returns:
        list[dict] | None: Une liste de dictionnaires, chaque dictionnaire représentant un utilisateur.
                            Retourne None en cas d'erreur.
    """
    print("\n⏳ Récupération de la liste des utilisateurs...")
    response_data = _make_api_get_request("/api/users")

    if not response_data:
        return None

    users_list = []
    # La réponse de l'API est un dictionnaire avec une clé 'users' qui contient la liste
    for user in response_data.get('users', []):
        users_list.append({
            "username": user.get('username'),
            "user_id": user.get('id')
        })
        
    print(f"✅ {len(users_list)} utilisateurs trouvés.")
    return users_list


# --- Point d'entrée du script ---
if __name__ == "__main__":
    if "your-graylog-instance" in GRAYLOG_BASE_URL:
        print("Veuillez configurer la variable d'environnement GRAYLOG_URL.", file=sys.stderr)
        sys.exit(1)
    
    if not API_TOKEN:
        print("Veuillez configurer la variable d'environnement GRAYLOG_API_TOKEN.", file=sys.stderr)
        sys.exit(1)

    all_streams = get_all_streams()
    all_users = get_all_users()

    # Affichage des résultats
    if all_streams:
        print("\n--- Liste des Streams ---")
        # json.dumps est utilisé pour un affichage "joli" (pretty-print)
        print(json.dumps(all_streams, indent=2, ensure_ascii=False))

    if all_users:
        print("\n--- Liste des Utilisateurs ---")
        print(json.dumps(all_users, indent=2, ensure_ascii=False))

    if not all_streams and not all_users:
        print("\nAucune donnée n'a pu être récupérée de Graylog.")

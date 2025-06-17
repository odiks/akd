#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import os
import sys

# --- Configuration ---
# Il est recommandé de ne pas coder en dur les informations sensibles.
# Utilisez des variables d'environnement pour plus de sécurité et de flexibilité.
GRAYLOG_BASE_URL = os.getenv("GRAYLOG_URL", "http://GRAYLOG-URL:9000")
API_TOKEN = os.getenv("GRAYLOG_API_TOKEN")

def assign_user_to_stream(stream_id: str, owner_user_id: str, users_to_add: str):
    """
    Assigne un nouvel utilisateur à un stream Graylog avec un rôle spécifique.

    Cette fonction met à jour les permissions d'un stream pour inclure un nouvel utilisateur.
    Elle préserve le propriétaire ("owner") existant du stream.

    Args:
        stream_id (str): L'ID du stream à modifier.
        owner_user_id (str): L'ID de l'utilisateur qui est propriétaire du stream.
        new_user_id (str): L'ID du nouvel utilisateur à ajouter.
        new_user_role (str, optional): Le rôle à assigner au nouvel utilisateur.
                                       Par défaut, "view".
    
    Returns:
        bool: True si l'opération a réussi, False sinon.
    """
    if not API_TOKEN:
        print("Erreur: La variable d'environnement GRAYLOG_API_TOKEN n'est pas définie.", file=sys.stderr)
        return False

    # Construction de l'URL de l'API pour la ressource (le stream)
    # Le format est /api/authz/shares/entities/{entity_grn}
    entity_grn = f"grn::::stream:{stream_id}"
    api_url = f"{GRAYLOG_BASE_URL}/authz/shares/entities/{entity_grn}"

    # Préparation des headers de la requête HTTP
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-Requested-By': 'python-script-stream-sharing' # Bonne pratique pour identifier l'origine de la requête
    }

    # Préparation du payload (le corps de la requête)
    # C'est un dictionnaire Python qui sera automatiquement converti en JSON par la bibliothèque `requests`.
    # Nous incluons le propriétaire existant et le nouvel utilisateur.
    # payload = {
    #     "selected_grantee_capabilities": {
   #          f"grn::::user:{owner_user_id}": "own",
   #          f"grn::::user:{new_user_id}": new_user_role
     #    }
   #  }

    # 1. Initialiser le dictionnaire des permissions avec le propriétaire
    capabilities_dict = {
        f"grn::::user:{owner_user_id}": "own"
    }
    
    # 2. Parcourir la liste des utilisateurs et les ajouter au dictionnaire
    for user in users_to_add:
        user_id = user['id']
        user_role = user['role']
        # Construire le GRN de l'utilisateur
        user_grn = f"grn::::user:{user_id}"
        # Ajouter la paire clé/valeur au dictionnaire
        capabilities_dict[user_grn] = user_role
    
    # 3. Construire le payload final
    payload = {
        "selected_grantee_capabilities": capabilities_dict
    }
    
    print(f"Tentative d'assignation de l'utilisateur '{users_to_add}' au stream '{stream_id}' avec le rôle ...")
    
    try:
        # La commande curl sans spécification de méthode (-X) mais avec des données (--data-raw)
        # est généralement un POST. Cependant, pour mettre à jour une ressource existante,
        # l'API REST de Graylog utilise une requête PUT pour cet endpoint.
        response = requests.post(
            api_url,
            headers=headers,
            json=payload,  # `json=` sérialise automatiquement le dictionnaire `payload` en JSON
            auth=(API_TOKEN, 'token') # Gère l'authentification Basic Auth, équivalent à `curl -u`
        )

        # Lève une exception si la requête a échoué (codes de statut 4xx ou 5xx)
        response.raise_for_status()

        # Un succès pour cette requête est souvent un code 204 (No Content)
        if response.status_code == 204:
            print(f"✅ Succès ! L'utilisateur '{new_user_id}' a été ajouté au stream '{stream_id}'.")
            return True
        else:
            print(f"✅ Succès avec le code {response.status_code}.")
            print("Réponse de l'API :", response.json())
            return True

    except requests.exceptions.HTTPError as http_err:
        print(f"❌ Erreur HTTP: {http_err}", file=sys.stderr)
        print(f"Réponse de l'API (code {http_err.response.status_code}): {http_err.response.text}", file=sys.stderr)
    except requests.exceptions.RequestException as err:
        print(f"❌ Erreur de requête: {err}", file=sys.stderr)
    
    return False

# --- Point d'entrée du script ---
if __name__ == "__main__":
    # Remplacez ces valeurs par celles qui correspondent à votre cas d'usage
    TARGET_STREAM_ID = "YOUR_STREAM_ID"
    OWNER_USER_ID = "ID_OF_THE_OWNER_USER"


    # Voici comment définir la liste des utilisateurs à ajouter
    # C'est une liste de dictionnaires. Chaque dictionnaire a une clé 'id' et 'role
    USERS_TO_SET = [
        {'id': 'jdoe', 'role': 'view'},
        {'id': 'msmith', 'role': 'edit'},
        {'id': 's-api', 'role': 'reader'}  # 'reader' est un alias commun pour 'view'
    ]

    if "YOUR_STREAM_ID" in TARGET_STREAM_ID:
        print("Veuillez éditer le script pour définir les variables TARGET_STREAM_ID, OWNER_USER_ID, et .")
    else:
        assign_user_to_stream(TARGET_STREAM_ID, OWNER_USER_ID, USERS_TO_SET)

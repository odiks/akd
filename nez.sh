#!/usr/bin/env bash
# =============================================================================
# redbutton_state.sh
# =============================================================================
set -euo pipefail
IFS=$'\n\t'

# --- Config ---
readonly ES_NODES=(
    "192.168.1.10:9200"
    "192.168.1.11:9200"
    "192.168.1.12:9200"
)
readonly ES_INDEX="redbutton"
readonly ES_DOC_ID="1"
readonly STATE_FILE="/DATA/logstash/dictionary/redbutton_state.yml"
readonly API_KEY_FILE="/etc/elastic/apikey.secret"
readonly CURL_TIMEOUT=5       # secondes avant de passer au node suivant
readonly CURL_MAX_TIME=10     # temps total max par tentative

# --- Lecture API Key ---
if [[ ! -f "${API_KEY_FILE}" ]]; then
    echo "[ERROR] Secret file not found: ${API_KEY_FILE}" >&2
    exit 1
fi

API_KEY_PERMS=$(stat -c "%a" "${API_KEY_FILE}")
if [[ "${API_KEY_PERMS}" != "600" ]]; then
    echo "[ERROR] Incorrect permissions on ${API_KEY_FILE}: ${API_KEY_PERMS} (expected: 600)" >&2
    exit 1
fi

ES_API_KEY="$(tr -d '[:space:]' < "${API_KEY_FILE}")"
if [[ -z "${ES_API_KEY}" ]]; then
    echo "[ERROR] Secret file is empty: ${API_KEY_FILE}" >&2
    exit 1
fi

# --- Dépendances ---
for cmd in curl jq; do
    if ! command -v "${cmd}" &>/dev/null; then
        echo "[ERROR] Missing dependency: ${cmd}" >&2
        exit 1
    fi
done

# --- Répertoire de destination ---
STATE_DIR="$(dirname "${STATE_FILE}")"
if [[ ! -d "${STATE_DIR}" ]]; then
    echo "[ERROR] Target directory does not exist: ${STATE_DIR}" >&2
    exit 1
fi

# --- Fichier temporaire réponse ---
TMP_RESPONSE=$(mktemp /tmp/es_response_XXXXXX.json)
trap 'rm -f "${TMP_RESPONSE}"' EXIT

# =============================================================================
# Fonction : query_node
# Tente une requête sur un node ES donné
# Retourne 0 si succès HTTP 200, 1 sinon
# =============================================================================
query_node() {
    local node_url="${1}"
    local url="${node_url}/${ES_INDEX}/_source/${ES_DOC_ID}"

    echo "[INFO] Trying node: ${node_url}" >&2

    local http_code
    http_code=$(curl -s \
        --connect-timeout "${CURL_TIMEOUT}" \
        --max-time "${CURL_MAX_TIME}" \
        -o "${TMP_RESPONSE}" \
        -w "%{http_code}" \
        -H "Authorization: ApiKey ${ES_API_KEY}" \
        "${url}" 2>/dev/null) || true
    # ⚠️ || true — on capture l'échec curl sans stopper le script (set -e)
    # Le code HTTP sera vide si curl échoue (timeout, réseau)

    if [[ "${http_code}" == "200" ]]; then
        echo "[INFO] Node responded successfully: ${node_url}" >&2
        return 0
    fi

    echo "[WARN] Node ${node_url} failed — HTTP: '${http_code:-no_response}'" >&2
    return 1
}

# =============================================================================
# Failover : parcourt les nodes jusqu'au premier qui répond
# =============================================================================
ES_RESPONSE_OK=false

for node in "${ES_NODES[@]}"; do
    if query_node "${node}"; then
        ES_RESPONSE_OK=true
        break
    fi
    echo "[WARN] Moving to next node..." >&2
done

if [[ "${ES_RESPONSE_OK}" != "true" ]]; then
    echo "[ERROR] All Elasticsearch nodes are unreachable — aborting" >&2
    exit 1
fi

# --- JSON extraction ---
STATE_SPLUNK=$(jq -r '.redbutton_splunk // "OFF"' "${TMP_RESPONSE}")
STATE_OSS=$(jq -r '.redbutton_oss    // "OFF"' "${TMP_RESPONSE}")

# --- Validation ---
for var_name in STATE_SPLUNK STATE_OSS; do
    val="${!var_name}"
    if [[ "${val}" != "ON" && "${val}" != "OFF" ]]; then
        echo "[WARN] Unexpected value ${var_name}='${val}', falling back to OFF" >&2
        printf -v "${var_name}" '%s' "OFF"
    fi
done

# --- Atomic write ---
TMP_YAML=$(mktemp "${STATE_DIR}/.redbutton_state.XXXXXX")
trap 'rm -f "${TMP_RESPONSE}" "${TMP_YAML}"' EXIT

printf 'state_splunk: %s\nstate_oss: %s\n' \
    "${STATE_SPLUNK}" "${STATE_OSS}" > "${TMP_YAML}"

chmod 640 "${TMP_YAML}"
mv "${TMP_YAML}" "${STATE_FILE}"

echo "[OK] ${STATE_FILE} updated — splunk=${STATE_SPLUNK} oss=${STATE_OSS}"

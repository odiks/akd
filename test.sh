#!/usr/bin/env bash
# =============================================================================
# redbutton_state.sh
# Retrieves redbutton states from Elasticsearch and generates a YAML file
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
readonly CURL_TIMEOUT=5
readonly CURL_MAX_TIME=10

# --- Email config ---
readonly MAIL_TO="ops-team@mydomain.com"
readonly MAIL_FROM="redbutton@mydomain.com"
readonly SMTP_SERVER="smtp.mydomain.com"

# =============================================================================
# API Key loading
# =============================================================================
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

# =============================================================================
# Dependency check
# =============================================================================
for cmd in curl jq mail; do
    if ! command -v "${cmd}" &>/dev/null; then
        echo "[ERROR] Missing dependency: ${cmd}" >&2
        exit 1
    fi
done

# =============================================================================
# Target directory check
# =============================================================================
STATE_DIR="$(dirname "${STATE_FILE}")"
if [[ ! -d "${STATE_DIR}" ]]; then
    echo "[ERROR] Target directory does not exist: ${STATE_DIR}" >&2
    exit 1
fi

# =============================================================================
# Elasticsearch response temp file
# =============================================================================
TMP_RESPONSE=$(mktemp /tmp/es_response_XXXXXX.json)
trap 'rm -f "${TMP_RESPONSE}"' EXIT

# =============================================================================
# Function : query_node
# Attempts a request on a given ES node — returns 0 if HTTP 200, 1 otherwise
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

    if [[ "${http_code}" == "200" ]]; then
        echo "[INFO] Node responded successfully: ${node_url}" >&2
        return 0
    fi

    echo "[WARN] Node ${node_url} failed — HTTP: '${http_code:-no_response}'" >&2
    return 1
}

# =============================================================================
# Function : send_alert_email
# Called ONLY when a state transition is detected
# =============================================================================
send_alert_email() {
    local service="${1}"
    local old_state="${2}"
    local new_state="${3}"
    local timestamp
    timestamp="$(date '+%Y-%m-%d %H:%M:%S')"

    local subject="[REDBUTTON] ${service} state changed: ${old_state} → ${new_state}"
    local body
    body=$(cat <<EOF
RedButton Alert
===============
Timestamp : ${timestamp}
Service   : ${service}
Old state : ${old_state}
New state : ${new_state}

Host      : $(hostname -f)
State file: ${STATE_FILE}

--
RedButton monitoring
EOF
)

    echo "${body}" | mail \
        -s "${subject}" \
        -r "${MAIL_FROM}" \
        -S smtp="${SMTP_SERVER}" \
        "${MAIL_TO}"

    echo "[INFO] Alert email sent to ${MAIL_TO} — ${service}: ${old_state} → ${new_state}" >&2
}

# =============================================================================
# Elasticsearch node failover
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

# =============================================================================
# JSON extraction
# Values are arrays — extracting first element [0]
# =============================================================================
STATE_SPLUNK=$(jq -r 'if (.redbutton_splunk | type) == "array"
    then .redbutton_splunk[0]
    else .redbutton_splunk
    end // "OFF"' "${TMP_RESPONSE}")

STATE_OSS=$(jq -r 'if (.redbutton_oss | type) == "array"
    then .redbutton_oss[0]
    else .redbutton_oss
    end // "OFF"' "${TMP_RESPONSE}")

# Log any extra values in the array (e.g. "EOM")
SPLUNK_EXTRA=$(jq -r '.redbutton_splunk[1:] | join(",")' "${TMP_RESPONSE}")
OSS_EXTRA=$(jq -r '.redbutton_oss[1:] | join(",")' "${TMP_RESPONSE}")

if [[ -n "${SPLUNK_EXTRA}" ]]; then
    echo "[INFO] redbutton_splunk extra values ignored: ${SPLUNK_EXTRA}" >&2
fi
if [[ -n "${OSS_EXTRA}" ]]; then
    echo "[INFO] redbutton_oss extra values ignored: ${OSS_EXTRA}" >&2
fi

# =============================================================================
# Value validation — ON or OFF only
# =============================================================================
for var_name in STATE_SPLUNK STATE_OSS; do
    val="${!var_name}"
    if [[ "${val}" != "ON" && "${val}" != "OFF" ]]; then
        echo "[WARN] Unexpected value ${var_name}='${val}', falling back to OFF" >&2
        printf -v "${var_name}" '%s' "OFF"
    fi
done

# =============================================================================
# Read previous state from existing YAML file
# → no extra file needed — read before overwriting
# =============================================================================
PREV_SPLUNK="OFF"
PREV_OSS="OFF"

if [[ -f "${STATE_FILE}" ]]; then
    PREV_SPLUNK=$(grep '^state_splunk:' "${STATE_FILE}" | awk '{print $2}' || echo "OFF")
    PREV_OSS=$(grep '^state_oss:'    "${STATE_FILE}" | awk '{print $2}' || echo "OFF")
    echo "[INFO] Previous state read from ${STATE_FILE} — splunk=${PREV_SPLUNK} oss=${PREV_OSS}" >&2
fi

# =============================================================================
# State transition detection + email alert on OFF → ON
# =============================================================================
if [[ "${PREV_SPLUNK}" != "${STATE_SPLUNK}" ]]; then
    echo "[INFO] SPLUNK state changed: ${PREV_SPLUNK} → ${STATE_SPLUNK}" >&2
    if [[ "${STATE_SPLUNK}" == "ON" ]]; then
        send_alert_email "SPLUNK" "${PREV_SPLUNK}" "${STATE_SPLUNK}"
    fi
fi

if [[ "${PREV_OSS}" != "${STATE_OSS}" ]]; then
    echo "[INFO] OSS state changed: ${PREV_OSS} → ${STATE_OSS}" >&2
    if [[ "${STATE_OSS}" == "ON" ]]; then
        send_alert_email "OSS" "${PREV_OSS}" "${STATE_OSS}"
    fi
fi

# =============================================================================
# Atomic YAML write
#
# Output in redbutton_state.yml :
#   state_splunk: ON
#   state_oss:    OFF
#   prev_splunk:  OFF
#   prev_oss:     OFF
#   last_update:  2026-04-27T10:32:00
# =============================================================================
LAST_UPDATE="$(date '+%Y-%m-%dT%H:%M:%S')"

TMP_YAML=$(mktemp "${STATE_DIR}/.redbutton_state.XXXXXX")
trap 'rm -f "${TMP_RESPONSE}" "${TMP_YAML}"' EXIT

printf 'state_splunk: %s\nstate_oss: %s\nprev_splunk: %s\nprev_oss: %s\nlast_update: %s\n' \
    "${STATE_SPLUNK}" \
    "${STATE_OSS}" \
    "${PREV_SPLUNK}" \
    "${PREV_OSS}" \
    "${LAST_UPDATE}" > "${TMP_YAML}"

chmod 640 "${TMP_YAML}"
mv "${TMP_YAML}" "${STATE_FILE}"

echo "[OK] ${STATE_FILE} updated — splunk=${STATE_SPLUNK} (was ${PREV_SPLUNK}) oss=${STATE_OSS} (was ${PREV_OSS})"

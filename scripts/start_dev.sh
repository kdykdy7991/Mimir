#!/usr/bin/env bash

set -euo pipefail

# Local development launcher.
#
# The OpenAI Python client uses httpx, which rejects proxy URLs using the
# generic "socks://" scheme. More importantly, the local Web API and the
# intranet embedding service should never be sent through an outbound proxy.
# These changes apply only to this process and its children; they do not alter
# the user's global shell configuration.

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
EMBEDDING_HOST="${SKDY_EMBEDDING_HOST:-10.113.114.88}"
API_PORT="${SKDY_API_PORT:-8766}"
WEB_PORT="${SKDY_WEB_PORT:-3008}"
MCP_PORT="${SKDY_MCP_PORT:-8765}"
API_HOST="${SKDY_API_HOST:-0.0.0.0}"
WEB_HOST="${SKDY_WEB_HOST:-0.0.0.0}"
MCP_HOST="${SKDY_MCP_HOST:-0.0.0.0}"
MCP_PATH="${SKDY_MCP_PATH:-/mcp}"
PUBLIC_HOST="${SKDY_PUBLIC_HOST:-}"

die() {
    printf 'start_dev.sh: %s\n' "$*" >&2
    exit 1
}

for command in awk hostname make npm ss; do
    command -v "${command}" >/dev/null 2>&1 \
        || die "required command not found: ${command}"
done

[[ -x "${PROJECT_DIR}/.venv/bin/python" ]] \
    || die "Python virtual environment is missing; run: make install"
[[ -f "${PROJECT_DIR}/web/package.json" ]] \
    || die "web/package.json is missing"
[[ -d "${PROJECT_DIR}/web/node_modules" ]] \
    || die "frontend dependencies are missing; run: make install"

if [[ -z "${PUBLIC_HOST}" ]]; then
    PUBLIC_HOST="$(hostname -I | awk '{print $1}')"
fi
[[ -n "${PUBLIC_HOST}" ]] \
    || die "cannot detect LAN address; set SKDY_PUBLIC_HOST explicitly"

for port_name in MCP_PORT API_PORT WEB_PORT; do
    port="${!port_name}"
    [[ "${port}" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) \
        || die "${port_name} must be an integer between 1 and 65535 (got: ${port})"
done
[[ "${MCP_PORT}" != "${API_PORT}" \
    && "${MCP_PORT}" != "${WEB_PORT}" \
    && "${API_PORT}" != "${WEB_PORT}" ]] \
    || die "SKDY_MCP_PORT, SKDY_API_PORT and SKDY_WEB_PORT must use different ports"
[[ "${MCP_PATH}" == /* ]] \
    || die "SKDY_MCP_PATH must start with / (got: ${MCP_PATH})"

assert_port_available() {
    local name="$1"
    local port="$2"

    if [[ -n "$(ss -H -ltn "sport = :${port}")" ]]; then
        ss -ltnp "sport = :${port}" >&2 || true
        die "${name} port ${port} is already in use; choose another with SKDY_${name}_PORT"
    fi
}

# Check all ports before starting any service, preventing partial startup.
assert_port_available MCP "${MCP_PORT}"
assert_port_available API "${API_PORT}"
assert_port_available WEB "${WEB_PORT}"

unset ALL_PROXY all_proxy

append_no_proxy() {
    local current="${1:-}"
    local value="$2"

    case ",${current}," in
        *",${value},"*) printf '%s' "${current}" ;;
        *)
            if [[ -n "${current}" ]]; then
                printf '%s' "${current},${value}"
            else
                printf '%s' "${value}"
            fi
            ;;
    esac
}

DEV_NO_PROXY="${NO_PROXY:-${no_proxy:-}}"
for host in 127.0.0.1 localhost "${PUBLIC_HOST}" "${EMBEDDING_HOST}"; do
    DEV_NO_PROXY="$(append_no_proxy "${DEV_NO_PROXY}" "${host}")"
done
export NO_PROXY="${DEV_NO_PROXY}"
export no_proxy="${DEV_NO_PROXY}"
export PORT="${WEB_PORT}"
export NEXT_PUBLIC_API_BASE_URL=""
export INTERNAL_API_BASE_URL="http://127.0.0.1:${API_PORT}"
export NEXT_ALLOWED_DEV_ORIGINS="${SKDY_NEXT_ALLOWED_DEV_ORIGINS:-${PUBLIC_HOST},127.0.0.1,localhost}"

echo "Starting SKDY RAG development services"
echo "  Web UI local: http://localhost:${WEB_PORT}"
echo "  Web UI LAN:   http://${PUBLIC_HOST}:${WEB_PORT}"
echo "  MCP endpoint: http://${PUBLIC_HOST}:${MCP_PORT}${MCP_PATH} (bind ${MCP_HOST})"
echo "  Web API:      http://${PUBLIC_HOST}:${API_PORT} (bind ${API_HOST})"
echo "  API docs:     http://${PUBLIC_HOST}:${API_PORT}/docs"
echo "  Embedding:    http://${EMBEDDING_HOST}:8003/v1"
echo "  Next origins: ${NEXT_ALLOWED_DEV_ORIGINS}"
echo "  Proxy bypass: ${NO_PROXY}"

cd "${PROJECT_DIR}"
exec make dev \
    MCP_HOST="${MCP_HOST}" MCP_PORT="${MCP_PORT}" MCP_PATH="${MCP_PATH}" \
    API_HOST="${API_HOST}" API_PORT="${API_PORT}" \
    WEB_HOST="${WEB_HOST}" WEB_PORT="${WEB_PORT}"

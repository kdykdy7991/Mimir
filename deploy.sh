#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_TIMEOUT="${DEPLOY_TIMEOUT:-300}"

die() {
    printf 'deploy: %s\n' "$*" >&2
    exit 1
}

for command in git docker; do
    command -v "${command}" >/dev/null 2>&1 || die "required command not found: ${command}"
done
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin is unavailable"

cd "${PROJECT_DIR}"

if [[ "${SKDY_SKIP_GIT_PULL:-0}" != "1" && "${SKDY_DEPLOY_SYNCED:-0}" != "1" ]]; then
    [[ -z "$(git status --porcelain --untracked-files=no)" ]] \
        || die "tracked files have local changes; commit or stash them before deployment"
    git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1 \
        || die "the current branch has no upstream; configure one with git push -u"

    echo ">>> Syncing the current branch from its upstream"
    git pull --ff-only
    # Continue with the script version from the newly pulled revision.
    exec env SKDY_DEPLOY_SYNCED=1 "${PROJECT_DIR}/deploy.sh"
elif [[ "${SKDY_SKIP_GIT_PULL:-0}" == "1" ]]; then
    echo ">>> Skipping Git sync (SKDY_SKIP_GIT_PULL=1)"
else
    echo ">>> Git sync completed"
fi

echo ">>> Validating Docker Compose configuration"
docker compose config --quiet

echo ">>> Building and starting the complete stack"
if ! docker compose up \
    --detach \
    --build \
    --remove-orphans \
    --wait \
    --wait-timeout "${DEPLOY_TIMEOUT}"; then
    echo ">>> Deployment failed; current status and recent logs follow" >&2
    docker compose ps >&2 || true
    docker compose logs --tail=200 >&2 || true
    exit 1
fi

PUBLIC_HOST="${SKDY_PUBLIC_HOST:-127.0.0.1}"

echo ">>> Deployment succeeded"
docker compose ps
printf 'Web UI:   http://%s:3000\n' "${PUBLIC_HOST}"
printf 'API docs: http://%s:8766/docs\n' "${PUBLIC_HOST}"
printf 'MCP:      http://%s:8765/mcp\n' "${PUBLIC_HOST}"

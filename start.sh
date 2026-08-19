#!/usr/bin/env bash
set -uo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

if [[ "${NO_COLOR+x}" == "x" ]]; then
    CYAN=""; YELLOW=""; RED=""; WHITE=""; RESET=""
else
    CYAN=$'\033[96m'; YELLOW=$'\033[33m'; RED=$'\033[91m'; WHITE=$'\033[97m'; RESET=$'\033[0m'
fi

if [[ ! -x "$PYTHON" ]]; then
    printf '%sRoadProof is not installed yet.%s\n' "$YELLOW" "$RESET"
    printf 'Run %sbash ./installer.sh%s once, then run this script again.\n' "$WHITE" "$RESET"
    exit 2
fi

MAPS_URL=""
if [[ $# -gt 0 && "${1:-}" != -* ]]; then
    MAPS_URL="$1"
    shift
else
    printf '%sRoadProof route analyzer%s\n' "$CYAN" "$RESET"
    printf 'Paste the Google Maps route link: '
    IFS= read -r MAPS_URL
fi

if [[ -z "${MAPS_URL//[[:space:]]/}" ]]; then
    printf '%sNo link was entered.%s\n' "$RED" "$RESET"
    exit 2
fi

export PYTHONIOENCODING=utf-8
cd "$PROJECT_ROOT"
exec "$PYTHON" -m roadproof --url "$MAPS_URL" "$@"

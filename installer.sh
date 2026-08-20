#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$PROJECT_ROOT/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"

if [[ "${NO_COLOR+x}" == "x" ]]; then
    CYAN=""; GREEN=""; YELLOW=""; RED=""; GRAY=""; WHITE=""; RESET=""
else
    CYAN=$'\033[96m'; GREEN=$'\033[92m'; YELLOW=$'\033[33m'; RED=$'\033[91m'
    GRAY=$'\033[90m'; WHITE=$'\033[97m'; RESET=$'\033[0m'
fi

step() {
    printf '\n%s==> %s%s\n' "$CYAN" "$1" "$RESET"
}

fail() {
    printf '\n%sSetup stopped: %s%s\n' "$RED" "$1" "$RESET" >&2
    exit 1
}

python_is_supported() {
    "$1" -c 'import platform, sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] < (3, 15) and platform.python_implementation() == "CPython" else 1)' >/dev/null 2>&1
}

find_python() {
    local candidate brew_prefix
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && python_is_supported "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done

    if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
        brew_prefix="$(brew --prefix python@3.12 2>/dev/null || true)"
        if [[ -n "$brew_prefix" ]]; then
            candidate="$brew_prefix/bin/python3.12"
            if [[ -x "$candidate" ]] && python_is_supported "$candidate"; then
                printf '%s\n' "$candidate"
                return 0
            fi
        fi
    fi
    return 1
}

run_as_root() {
    if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        printf '%sAdministrator permission is required to install Python.%s\n' "$YELLOW" "$RESET"
        sudo "$@"
    else
        fail "Python must be installed by an administrator; sudo is unavailable."
    fi
}

install_python() {
    local platform
    platform="$(uname -s)"

    if [[ "$platform" == "Darwin" ]]; then
        if ! command -v brew >/dev/null 2>&1; then
            fail "CPython 3.10-3.14 was not found. Install it from https://www.python.org/downloads/macos/ or install Homebrew, then rerun installer.sh."
        fi
        step "Installing Python 3.12 with Homebrew"
        brew install python@3.12
        return
    fi

    if [[ "$platform" != "Linux" ]]; then
        fail "Unsupported operating system: $platform"
    fi

    if command -v apt-get >/dev/null 2>&1; then
        step "Installing Python and virtual-environment support with apt"
        run_as_root apt-get update
        run_as_root apt-get install -y python3 python3-venv python3-pip
    elif command -v dnf >/dev/null 2>&1; then
        step "Installing Python with dnf"
        run_as_root dnf install -y python3 python3-pip
    elif command -v pacman >/dev/null 2>&1; then
        step "Installing Python with pacman"
        run_as_root pacman -Sy --needed --noconfirm python python-pip
    elif command -v zypper >/dev/null 2>&1; then
        step "Installing Python with zypper"
        run_as_root zypper --non-interactive install python3 python3-pip
    else
        fail "Python 3.10+ was not found and no supported package manager is available."
    fi
}

ensure_venv_support() {
    local python="$1"
    if "$python" -m venv "$VENV_DIR" >/dev/null 2>&1; then
        return
    fi

    if [[ "$(uname -s)" == "Linux" ]] && command -v apt-get >/dev/null 2>&1; then
        step "Installing Python virtual-environment support"
        run_as_root apt-get install -y python3-venv
        "$python" -m venv "$VENV_DIR"
        return
    fi
    fail "Python is available, but its venv module could not create an isolated environment."
}

cd "$PROJECT_ROOT"
printf '%sRoadProof cross-platform setup%s\n' "$GREEN" "$RESET"
printf '%sProject: %s%s\n' "$GRAY" "$PROJECT_ROOT" "$RESET"

PYTHON="$(find_python || true)"
if [[ -z "$PYTHON" ]]; then
    install_python
    PYTHON="$(find_python || true)"
fi
[[ -n "$PYTHON" ]] || fail "Python was installed but a supported interpreter was not found. Open a new terminal and rerun installer.sh."

VERSION="$($PYTHON -c 'import platform; print(platform.python_version())')"
printf '%sPython %s detected%s\n' "$GREEN" "$VERSION" "$RESET"

step "Creating an isolated RoadProof environment"
if [[ ! -x "$VENV_PYTHON" ]]; then
    ensure_venv_support "$PYTHON"
fi

step "Updating Python installation tooling"
"$VENV_PYTHON" -m pip install --disable-pip-version-check --upgrade pip

step "Installing the official NumPy binary"
NUMPY_REQUIREMENT="$($VENV_PYTHON -c 'import sys; print("numpy==2.3.5" if sys.version_info >= (3, 14) else "numpy==2.2.6")')"
"$VENV_PYTHON" -m pip install --disable-pip-version-check --upgrade --force-reinstall \
    --only-binary=:all: "$NUMPY_REQUIREMENT"

step "Installing RoadProof requirements"
"$VENV_PYTHON" -m pip install --disable-pip-version-check --upgrade -r "$PROJECT_ROOT/requirements.txt"

step "Verifying compiled route-analysis dependencies"
"$VENV_PYTHON" -c 'import warnings; warnings.filterwarnings("error", message=r"Numpy built with MINGW-W64.*"); import mapbox_vector_tile, numpy, shapely; print(f"NumPy {numpy.__version__}; Shapely {shapely.__version__}; vector-tile decoder ready")'

step "Running the built-in self-check"
"$VENV_PYTHON" -m roadproof --self-check

chmod +x "$PROJECT_ROOT/installer.sh" "$PROJECT_ROOT/start.sh"
printf '\n%sRoadProof is ready.%s\n' "$GREEN" "$RESET"
printf 'Run %s./start.sh%s and paste a Google Maps route link.\n' "$WHITE" "$RESET"

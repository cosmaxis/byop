#!/bin/bash
set -e

# Read the complete script before running child processes. With curl | sh,
# children otherwise share the script's stdin and can consume the remaining
# commands before the shell reaches them.
{

# =============================================================================
# byop installer — Bring Your Own Provider
# Wire a custom OpenAI-compatible LLM provider into Zed, py.dev, and more.
# https://github.com/Cosmaxis/byop
# =============================================================================

REPO="Cosmaxis/byop"
BYOP_REPO_URL="${BYOP_REPO_URL:-https://github.com/${REPO}.git}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
DIM='\033[2m'
BOLD='\033[1m'
NC='\033[0m'

CHECK="${GREEN}✓${NC}"
CROSS="${RED}✗${NC}"
WARN="${YELLOW}!${NC}"

info()  { printf "  %b\n" "$1"; }
die()   { printf "  %b %b\n" "$CROSS" "$1" >&2; exit 1; }

# =============================================================================
# Preconditions
# =============================================================================

OS="$(uname -s)"
if [ "$OS" != "Darwin" ]; then
    printf "%b\n" "${YELLOW}byop currently supports macOS only${NC} (it integrates with the macOS keychain)."
    printf "%b\n" "Detected: ${OS}. Aborting."
    exit 1
fi

# Locate a Python >= 3.11 interpreter.
find_python() {
    for cand in python3.13 python3.12 python3.11 python3; do
        if command -v "$cand" >/dev/null 2>&1; then
            if "$cand" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
                command -v "$cand"
                return 0
            fi
        fi
    done
    return 1
}

# Resolve the latest release tag (falls back to the default branch).
latest_tag() {
    curl -sfS "https://api.github.com/repos/${REPO}/releases/latest" 2>/dev/null \
        | grep '"tag_name"' | head -1 \
        | sed 's/.*"tag_name" *: *"//;s/".*//'
}

# =============================================================================
# Main
# =============================================================================

printf "\n"
printf "%b\n" "${BOLD}byop${NC} ${DIM}— Bring Your Own Provider${NC}"
printf "%b\n" "${DIM}Custom OpenAI-compatible LLM provider setup for your AI coding tools${NC}"
printf "\n"

printf "%b\n" "${DIM}[1/4] Checking prerequisites${NC}"
# curl is part of macOS and is also needed to download this installer.
command -v curl >/dev/null 2>&1 || die "curl is unavailable. Restore the macOS system tools and retry."


# --- Homebrew and Python ----------------------------------------------------
# Homebrew may be installed but not yet on PATH in a non-login shell.
for brew_bin in /opt/homebrew/bin /usr/local/bin; do
    if [ -x "$brew_bin/brew" ] && ! command -v brew >/dev/null 2>&1; then
        PATH="$brew_bin:$PATH"
        export PATH
    fi
done

ensure_brew() {
    if command -v brew >/dev/null 2>&1; then
        return
    fi
    info "${WARN} Homebrew not found; installing it for missing prerequisites."
    info "Homebrew may request your macOS administrator password."
    BREW_INSTALLER="$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
        || die "Could not download the Homebrew installer. Check your network connection."
    if ( : </dev/tty ) 2>/dev/null; then
        /bin/bash -c "$BREW_INSTALLER" </dev/tty \
            || die "Homebrew installation failed. See the errors above."
    else
        NONINTERACTIVE=1 /bin/bash -c "$BREW_INSTALLER" \
            || die "Homebrew installation failed. Retry in an interactive terminal with administrator access."
    fi
    for brew_bin in /opt/homebrew/bin /usr/local/bin; do
        if [ -x "$brew_bin/brew" ]; then
            PATH="$brew_bin:$PATH"
            export PATH
            break
        fi
    done
    command -v brew >/dev/null 2>&1 || die "Homebrew was installed but could not be found on PATH."
}

PY="$(find_python || true)"
if [ -z "$PY" ]; then
    ensure_brew
    info "${WARN} Python 3.11+ not found; installing Python via Homebrew..."
    brew install python@3.12 || die "Failed to install Python via Homebrew. See the errors above."
    PY="$(find_python || true)"
    if [ -z "$PY" ]; then
        PY="$(brew --prefix python@3.12)/bin/python3.12"
        [ -x "$PY" ] && "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)' \
            || die "Python was installed but a usable Python 3.11+ interpreter could not be found."
    fi
fi
info "${CHECK} Python: ${BOLD}$("$PY" --version 2>&1)${NC}"
printf "%b\n" "${DIM}[2/4] Checking Git${NC}"

# Git is needed by pipx to install from the Git repository. On macOS,
# /usr/bin/git can exist as an unusable Command Line Tools placeholder.
if ! git --version >/dev/null 2>&1; then
    ensure_brew
    info "${WARN} Git not found; installing it via Homebrew..."
    brew install git || die "Failed to install Git via Homebrew. See the errors above."
    PATH="$(brew --prefix)/bin:$PATH"
    export PATH
    hash -r
    git --version >/dev/null 2>&1 || die "Git was installed but could not be run."
fi
info "${CHECK} Git is available."

printf "%b\n" "${DIM}[3/4] Checking pipx${NC}"
# --- pipx (isolated install, including byop's Python dependencies) --------
if ! command -v pipx >/dev/null 2>&1; then
    ensure_brew
    info "${WARN} pipx not found; installing it via Homebrew..."
    brew install pipx || die "Failed to install pipx via Homebrew. See the errors above."
    PATH="$(brew --prefix)/bin:$PATH"
    export PATH
    hash -r
    command -v pipx >/dev/null 2>&1 || die "pipx was installed but could not be found on PATH."
fi
pipx ensurepath >/dev/null 2>&1 || true

# --- Determine version to install ------------------------------------------
TAG="$(latest_tag)"
if [ -n "$TAG" ]; then
    SOURCE="git+${BYOP_REPO_URL}@${TAG}"
    info "${CHECK} Latest release: ${BOLD}${TAG}${NC}"
else
    SOURCE="git+${BYOP_REPO_URL}"
    TAG="(default branch)"
    info "${WARN} Could not detect a release; installing from the default branch."
fi

printf "\n"
printf "%b\n" "${DIM}[4/4] Installing byop ${TAG}${NC}"

if pipx install --force --python "$PY" "$SOURCE"; then
    info "${CHECK} Installed with pipx and its Python dependencies"
else
    die "pipx install failed. Check the errors above and retry: pipx install --python \"$PY\" \"$SOURCE\""
fi

# --- Verify ----------------------------------------------------------------
printf "\n"
if command -v byop >/dev/null 2>&1; then
    printf "%b\n" "${GREEN}${BOLD}Installation complete${NC} ${DIM}($(byop --version 2>/dev/null))${NC}"
    printf "\n"
    printf "%b\n" "${DIM}Next: run ${CYAN}byop${NC} to configure your provider and choose your tools."
else
    BYOP_BIN="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || true)"
    BYOP_BIN="${BYOP_BIN:-${PIPX_BIN_DIR:-$HOME/.local/bin}}"
    printf "%b\n" "${GREEN}${BOLD}Installation complete${NC}"
    printf "\n"
    printf "%b\n" "${DIM}Next: run ${CYAN}\"$BYOP_BIN/byop\"${NC} to configure your provider and choose your tools."
    printf "%b\n" "${DIM}For the shorter ${CYAN}byop${NC} command, run ${CYAN}pipx ensurepath${NC} and open a new terminal.${NC}"
fi
printf "\n"
}

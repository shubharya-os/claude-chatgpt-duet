#!/usr/bin/env sh
# duet installer — installs the CLI, then tells you exactly what is still missing.
set -e

REPO_URL="${DUET_REPO:-https://github.com/diablo69-contri/duet}"
DIR="${DUET_DIR:-$HOME/.duet-src}"

say() { printf '%s\n' "$*"; }

PY=""
for candidate in python3 python3.12 python3.11 python3.10 python3.9; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  say "duet needs Python 3.9 or newer, and I could not find it."
  say "  macOS:  brew install python"
  say "  Debian: sudo apt install python3 python3-venv"
  exit 1
fi

if [ -d "$DIR/.git" ]; then
  say "updating $DIR"
  git -C "$DIR" pull --ff-only
else
  say "cloning $REPO_URL into $DIR"
  git clone --depth 1 "$REPO_URL" "$DIR"
fi

if command -v pipx >/dev/null 2>&1; then
  say "installing with pipx"
  pipx install --force "$DIR"
else
  say "installing with pip (--user)"
  # old pip installs the package without its entry point; upgrade first
  "$PY" -m pip install --user --quiet --upgrade pip >/dev/null 2>&1 || true
  "$PY" -m pip install --user --upgrade "$DIR"
fi

say ""
if command -v duet >/dev/null 2>&1; then
  duet doctor || true
else
  say "duet installed, but it is not on your PATH yet."
  say "Add your user bin directory to PATH, then run: duet doctor"
  say "  bash/zsh:  export PATH=\"\$($PY -m site --user-base)/bin:\$PATH\""
fi

#!/usr/bin/env sh
# One command to a working /duet:
#
#   curl -fsSL https://raw.githubusercontent.com/shubharya-os/claude-chatgpt-duet/main/install.sh | sh
#
# Installs duet, then runs `duet setup`, which installs the two agent CLIs if
# they are missing, signs you in to both, and adds /duet to Claude Code and
# Codex. Everything it does is something you could do by hand; it is short on
# purpose so you can read it before piping it to a shell.
set -e

REPO="${DUET_REPO:-https://github.com/shubharya-os/claude-chatgpt-duet}"

say() { printf '%s\n' "$*"; }
die() { printf '%s\n' "$*" >&2; exit 1; }

# --- python ----------------------------------------------------------------
PY=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
[ -n "$PY" ] || die "duet needs Python 3.9 or newer.
  macOS:  brew install python
  Debian: sudo apt install python3 python3-pip"

# --- install ---------------------------------------------------------------
# Three ways, in order of how well they behave. Most systems now ship Python as
# "externally managed" (PEP 668), where `pip install --user` refuses outright —
# so the fallback is a virtualenv duet owns, which always works.
say "installing duet with $PY"
DUET=""

if command -v pipx >/dev/null 2>&1; then
  pipx install --force "git+$REPO" >/dev/null 2>&1 && DUET="$(command -v duet 2>/dev/null || true)"
fi

if [ -z "$DUET" ]; then
  if "$PY" -m pip install --user --quiet --upgrade "git+$REPO" >/dev/null 2>&1; then
    DUET="$(command -v duet 2>/dev/null || true)"
    if [ -z "$DUET" ]; then
      USER_BIN="$("$PY" -m site --user-base 2>/dev/null)/bin"
      [ -x "$USER_BIN/duet" ] && DUET="$USER_BIN/duet"
    fi
  fi
fi

VENV="${DUET_HOME:-$HOME/.duet}/venv"
if [ -z "$DUET" ]; then
  say "using a virtualenv at $VENV (this Python is externally managed)"
  "$PY" -m venv "$VENV" || die "could not create a virtualenv at $VENV.
  On Debian/Ubuntu: sudo apt install python3-venv"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  "$VENV/bin/python" -m pip install --quiet "git+$REPO" ||
    die "could not install duet from $REPO"
  DUET="$VENV/bin/duet"
fi

[ -x "${DUET%% *}" ] || [ -n "$(command -v ${DUET%% *} 2>/dev/null)" ] ||
  die "duet installed but I cannot find the command"

# --- make it reachable -----------------------------------------------------
# A bare `duet` is what the docs say and what the /duet command will look for,
# so link it somewhere already on PATH when there is an obvious place.
case ":$PATH:" in
  *":$HOME/.local/bin:"*)
    if [ "$DUET" != "$(command -v duet 2>/dev/null || true)" ] && [ -d "$HOME/.local/bin" ]; then
      ln -sf "$DUET" "$HOME/.local/bin/duet" 2>/dev/null && DUET="$HOME/.local/bin/duet"
    fi
    ;;
esac

if [ -z "$(command -v duet 2>/dev/null || true)" ]; then
  say ""
  say "duet is installed at $DUET, which is not on your PATH."
  say "Add this line to your shell profile to make \`duet\` work everywhere:"
  say "    export PATH=\"$(dirname "$DUET"):\$PATH\""
  say "Everything below still works without it."
fi

# --- set it up -------------------------------------------------------------
say ""
if [ -t 0 ]; then
  # A real terminal: setup can ask before installing the agent CLIs.
  $DUET setup
else
  # Piped from curl, so stdin is the script itself and nothing can be asked.
  say "Installed. Finish with one more command, which needs a terminal it can"
  say "ask questions in:"
  say ""
  say "    $DUET setup"
  say ""
  say "It installs the two agent CLIs if missing, signs you in to both (no API"
  say "keys — your Claude and ChatGPT plans), and adds /duet to Claude Code."
fi

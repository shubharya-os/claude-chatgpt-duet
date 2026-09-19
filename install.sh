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

# Does this path hold a duet that actually runs? Being executable is not the
# same thing: a symlink into a virtualenv that has since been deleted, or a
# leftover shim from an older install, is executable and still cannot start.
# Nothing below is accepted as "installed" until it has answered --version.
duet_version() {
  [ -n "$1" ] || return 1
  out=$("$1" --version 2>/dev/null) || return 1
  case "$out" in *duet*) printf '%s\n' "$out" ;; *) return 1 ;; esac
}

duet_runs() { duet_version "$1" >/dev/null 2>&1; }

# --- python ----------------------------------------------------------------
# Names on PATH first, then the usual absolute locations. The second list is
# not belt-and-braces: on this project's own author's Mac, `python3` resolves to
# a broken x86 binary in /usr/local/bin and the working interpreter lives in
# /opt/homebrew/bin, which is not on PATH at all. Searching only PATH reported
# "no Python 3.9+" on a machine with Python 3.12 installed.
PY=""
for candidate in \
  python3 python3.13 python3.12 python3.11 python3.10 python3.9 python \
  /opt/homebrew/bin/python3 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
  /opt/homebrew/bin/python3.11 /usr/local/bin/python3 /usr/bin/python3 \
  /usr/local/opt/python@3.12/bin/python3.12 /opt/homebrew/opt/python@3.12/bin/python3.12
do
  case "$candidate" in
    /*) [ -x "$candidate" ] || continue ;;
    *)  command -v "$candidate" >/dev/null 2>&1 || continue ;;
  esac
  # It has to actually run: a binary for the wrong architecture is present,
  # executable, and useless.
  "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null || continue
  PY="$candidate"
  break
done
[ -n "$PY" ] || die "duet needs Python 3.9 or newer, and I could not find one that runs.
  Checked PATH and the usual locations. If you have one somewhere else, run:
    <your python> -m pip install --user git+$REPO
  macOS:  brew install python
  Debian: sudo apt install python3 python3-pip python3-venv"

# --- install ---------------------------------------------------------------
# Three ways, in order of how well they behave. Most systems now ship Python as
# "externally managed" (PEP 668), where `pip install --user` refuses outright —
# so the fallback is a virtualenv duet owns, which always works.
say "installing duet with $PY"
DUET=""

if command -v pipx >/dev/null 2>&1; then
  if pipx install --force "git+$REPO" >/dev/null 2>&1; then
    # pipx's own bin directory first: a `duet` already on PATH from an earlier
    # install would shadow the one just written, and reporting that one as the
    # result of this install is how a second run silently keeps an old version.
    for candidate in "${PIPX_BIN_DIR:-$HOME/.local/bin}/duet" "$(command -v duet 2>/dev/null || true)"; do
      if duet_runs "$candidate"; then DUET="$candidate"; break; fi
    done
  fi
fi

if [ -z "$DUET" ]; then
  if "$PY" -m pip install --user --quiet --upgrade "git+$REPO" >/dev/null 2>&1; then
    USER_BIN="$("$PY" -m site --user-base 2>/dev/null)/bin"
    for candidate in "$USER_BIN/duet" "$(command -v duet 2>/dev/null || true)"; do
      if duet_runs "$candidate"; then DUET="$candidate"; break; fi
    done
  fi
fi

VENV="${DUET_HOME:-$HOME/.duet}/venv"
if [ -z "$DUET" ]; then
  say "using a virtualenv at $VENV (this Python is externally managed)"
  "$PY" -m venv "$VENV" || die "could not create a virtualenv at $VENV.
  On Debian/Ubuntu: sudo apt install python3-venv"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
  # --upgrade, like the --user branch above. Without it, a second run over a
  # venv whose first install died half way gets "Requirement already
  # satisfied", pip does nothing, the console script is still missing, and the
  # rerun that was meant to repair the install cannot.
  "$VENV/bin/python" -m pip install --quiet --upgrade "git+$REPO" ||
    die "could not install duet from $REPO"
  DUET="$VENV/bin/duet"
fi

# Only reachable from the virtualenv branch: pipx and pip --user each accept
# their result only after duet_runs has passed, so $VENV is what is broken.
# Removing just the script would not do — pip would still believe duet is
# installed and the next run would repair nothing.
duet_runs "$DUET" || die "duet is at $DUET but will not run.
  It installed and then could not start, which usually means a half-finished
  earlier install is in the way. Remove it and try again:
      rm -rf \"$VENV\"
      curl -fsSL $REPO/raw/main/install.sh | sh"

# --- make it reachable -----------------------------------------------------
# A bare `duet` is what the docs say and what the /duet command will look for,
# so link it somewhere already on PATH when there is an obvious place.
case ":$PATH:" in
  *":$HOME/.local/bin:"*)
    # On PATH but not yet created is ordinary — plenty of shell profiles add
    # ~/.local/bin unconditionally. Make it rather than skipping the link and
    # telling the user to edit a profile that already does the right thing.
    mkdir -p "$HOME/.local/bin" 2>/dev/null || true
    if [ -d "$HOME/.local/bin" ] && [ "$DUET" != "$(command -v duet 2>/dev/null || true)" ]; then
      if ln -sf "$DUET" "$HOME/.local/bin/duet" 2>/dev/null &&
         duet_runs "$HOME/.local/bin/duet"; then
        DUET="$HOME/.local/bin/duet"
      fi
    fi
    ;;
esac

# Whether `duet` is on PATH is the wrong question: what matters is whether the
# `duet` on PATH is the one just installed. An older install earlier on PATH
# answers `command -v` perfectly well, and staying quiet about it means every
# `duet` the user types afterwards is the old one.
ON_PATH="$(command -v duet 2>/dev/null || true)"
THEIRS=""
if [ -n "$ON_PATH" ]; then
  THEIRS="$(duet_version "$ON_PATH" || true)"
fi
MINE="$(duet_version "$DUET" || true)"

if [ -z "$ON_PATH" ]; then
  say ""
  say "duet is installed at $DUET, which is not on your PATH."
  say "Add this line to your shell profile to make \`duet\` work everywhere:"
  say "    export PATH=\"$(dirname "$DUET"):\$PATH\""
  say "Everything below still works without it."
elif [ "$THEIRS" != "$MINE" ]; then
  # Covers both an older install and one that no longer runs at all: either
  # way it answers `duet` first and this install does not.
  say ""
  say "Careful: the \`duet\` on your PATH is not the one just installed."
  say "    on PATH:        $ON_PATH  (${THEIRS:-does not run})"
  say "    just installed: $DUET  ($MINE)"
  say "Put the new one first, or the old one is what you will keep running:"
  say "    export PATH=\"$(dirname "$DUET"):\$PATH\""
fi

# --- set it up -------------------------------------------------------------
say ""

# Piped from curl, stdin is the script itself, so `duet setup` has nothing to
# read an answer from. Reconnect to the terminal when there is one — that is
# what makes this a single command rather than "install, now run one more".
# `[ -r /dev/tty ]` is not enough: the file can exist and still fail to open
# ("Device not configured") in a session with no controlling terminal, and the
# shell prints that failure itself. Try it in a subshell where the noise can be
# discarded, and only redirect for real once it is known to work.
if [ ! -t 0 ] && (exec < /dev/tty) 2>/dev/null; then
  exec < /dev/tty
fi

if [ -t 0 ]; then
  $DUET setup
else
  # No terminal at all: a CI job, or a shell with no controlling tty. Nothing
  # can be asked, so say what is left rather than guessing at consent.
  say "Installed. One command left, in a terminal it can ask questions in:"
  say ""
  say "    $DUET setup"
  say ""
  say "It installs the two agent CLIs if missing, signs you in to both (no API"
  say "keys — your Claude and ChatGPT plans), and adds /duet to Claude Code."
  say ""
  say "Non-interactive? \`$DUET setup --yes\` installs without asking first."
fi

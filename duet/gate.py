"""Finding the command that decides whether the work is done.

The gate is the single most valuable thing you can give a session: the harness
runs it, both agents see the real output, and it outranks both their opinions.
A session without one is two models agreeing by argument alone.

So duet looks for it rather than making you remember the flag — and says what
it picked, because a gate you did not choose silently running the wrong command
is worse than no gate at all.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# Ordered: the first that matches a real signal in the project wins.
CANDIDATES: List[Tuple[str, str]] = [
    ("Makefile", "make test"),
    ("pyproject.toml", "pytest -q"),
    ("pytest.ini", "pytest -q"),
    ("tox.ini", "pytest -q"),
    ("setup.cfg", "pytest -q"),
    ("Cargo.toml", "cargo test"),
    ("go.mod", "go test ./..."),
    ("Gemfile", "bundle exec rspec"),
    ("mix.exs", "mix test"),
    ("build.gradle", "./gradlew test"),
    ("pom.xml", "mvn -q test"),
]


def _package_json_script(root: Path) -> Optional[str]:
    path = root / "package.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return None
    for name in ("test", "check", "ci"):
        script = scripts.get(name)
        # `npm init` writes a placeholder test script that only prints an error.
        if isinstance(script, str) and script.strip() and "no test specified" not in script:
            return "npm test" if name == "test" else "npm run %s" % name
    return None


def _make_has_test_target(root: Path) -> bool:
    try:
        text = (root / "Makefile").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return re.search(r"^test\s*:", text, re.MULTILINE) is not None


def _has_python_tests(root: Path) -> bool:
    if (root / "tests").is_dir() or (root / "test").is_dir():
        return True
    return any(root.glob("test_*.py")) or any(root.glob("*_test.py"))


def _runnable(command: str, root: str) -> bool:
    """Is the first word of this command something that exists?

    A detected gate that cannot start fails every single turn, which blocks
    consensus for the whole session — worse than having no gate, because the
    pair can never finish however right they are. Found by both agents in a
    live session: `pytest -q` was detected on a machine where pytest is only
    inside a virtualenv, so the gate could never pass.
    """
    first = command.split()[0]
    if shutil.which(first):
        return True
    # A relative launcher like ./gradlew
    candidate = Path(root) / first
    return candidate.is_file() and os.access(str(candidate), os.X_OK)


def _python_module_form(command: str) -> Optional[str]:
    """`pytest -q` becomes `<this python> -m pytest -q`, when that works.

    pytest is usually installed into a virtualenv rather than onto PATH, and
    the interpreter running duet is the one most likely to have it.
    """
    first, _, rest = command.partition(" ")
    if first not in ("pytest",):
        return None
    try:
        proc = subprocess.run(
            [sys.executable, "-m", first, "--version"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return "%s -m %s%s" % (sys.executable, first, (" " + rest) if rest else "")


def detect(root: str) -> Optional[str]:
    """A test command this project plausibly has, or None.

    Deliberately conservative: a wrong gate that always passes is worse than no
    gate, because it looks like verification and is not.
    """
    base = Path(root).expanduser().resolve()

    def usable(command: Optional[str]) -> Optional[str]:
        """Only hand back a command that can actually start."""
        if not command:
            return None
        if _runnable(command, str(base)):
            return command
        return _python_module_form(command)

    script = usable(_package_json_script(base))
    if script:
        return script

    for marker, command in CANDIDATES:
        if not (base / marker).is_file():
            continue
        if marker == "Makefile":
            if _make_has_test_target(base):
                return command
            continue
        if command.startswith("pytest") and not _has_python_tests(base):
            continue
        found = usable(command)
        if found:
            return found
        continue

    if _has_python_tests(base):
        return usable("pytest -q")
    return None


def describe(command: Optional[str]) -> str:
    if command:
        return "found a test command: %s" % command
    return (
        "no test command found — the pair can only agree by argument. "
        "Pass --gate \"<your tests>\" if you have one."
    )

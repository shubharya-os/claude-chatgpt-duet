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
import re
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


def detect(root: str) -> Optional[str]:
    """A test command this project plausibly has, or None.

    Deliberately conservative: a wrong gate that always passes is worse than no
    gate, because it looks like verification and is not.
    """
    base = Path(root).expanduser().resolve()

    script = _package_json_script(base)
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
        return command

    if _has_python_tests(base):
        return "pytest -q"
    return None


def describe(command: Optional[str]) -> str:
    if command:
        return "found a test command: %s" % command
    return (
        "no test command found — the pair can only agree by argument. "
        "Pass --gate \"<your tests>\" if you have one."
    )

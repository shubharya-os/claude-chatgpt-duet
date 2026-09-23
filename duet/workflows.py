"""Workflows: what ECC asks an agent to do, checked instead of asked.

Every orchestrated workflow in the agent-harness world is a prompt. "Write a
failing test first." "Do not change behaviour." "Only plan, do not build." The
agent is told the rule and trusted to follow it, and a single agent grading
its own homework will report that it did.

duet has two things a prompt does not: a harness that runs the gate itself,
and a veto over consensus. So each workflow here states its rule to the pair
*and* checks it against what actually happened — the gate's history and the
files on disk. When the check fails, the sign-offs are cleared and both agents
are told exactly which rule was broken. Neither of them can talk past it,
because neither of them is the one checking.

What each can and cannot prove is written next to it. None of these makes two
models incapable of being wrong together; each closes one specific way of
declaring done without having done it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Optional

# What a test file looks like, across the ecosystems duet detects gates for.
TEST_GLOBS = (
    "test_*.py", "*_test.py", "*_test.go", "*.test.js", "*.test.ts",
    "*.test.jsx", "*.test.tsx", "*.spec.js", "*.spec.ts", "*_test.rb",
    "*_spec.rb", "*Test.java", "*Tests.java", "*_test.rs", "*Test.kt",
)
TEST_DIRS = ("tests", "test", "spec", "__tests__")
SKIP_DIRS = {".duet", ".git", "__pycache__", "node_modules", ".venv", "venv",
             ".tox", "target", "dist", "build"}


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_test(rel: Path) -> bool:
    if any(part in TEST_DIRS for part in rel.parts[:-1]):
        return rel.suffix not in ("", ".md", ".txt", ".json", ".lock")
    return any(rel.match(pattern) for pattern in TEST_GLOBS)


def test_files(root: str) -> Dict[str, str]:
    """Every test file under root, mapped to a hash of its contents."""
    base = Path(root).expanduser().resolve()
    found: Dict[str, str] = {}
    if not base.is_dir():
        return found
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if SKIP_DIRS & set(rel.parts):
            continue
        if _is_test(rel):
            try:
                found[rel.as_posix()] = _digest(path)
            except OSError:
                continue
    return found


def all_files(root: str) -> Dict[str, str]:
    """Every file under root that is part of the work, hashed."""
    base = Path(root).expanduser().resolve()
    found: Dict[str, str] = {}
    if not base.is_dir():
        return found
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(base)
        if SKIP_DIRS & set(rel.parts):
            continue
        try:
            found[rel.as_posix()] = _digest(path)
        except OSError:
            continue
    return found


class Workflow:
    """A rule the pair is told, and a check that it was followed.

    `begin` records a baseline once — on resume it is already there and is
    left alone, because the rule is about the session as a whole, not about
    whatever state it happened to be resumed from. `on_gate` sees every gate
    result in order. `veto` is asked when both agents have signed off, and
    returns why that sign-off does not count, or None.
    """

    name = ""
    needs_gate = True

    def begin(self, root: str, state: Dict) -> None:
        pass

    def on_gate(self, state: Dict, digest: str, ok: bool) -> None:
        pass

    def veto(self, root: str, state: Dict) -> Optional[str]:
        return None


class Fix(Workflow):
    """A fix counts only once the bug has been seen to fail.

    Checks: some state in this session — the starting one, or one the pair
    produced — made the gate fail, and the state they signed off on passes it.
    That is the mechanical form of "reproduce it as a failing test first".

    Cannot check: that the red was *this* bug. A test that fails for an
    unrelated reason satisfies the letter of it. That part is the reviewer's
    job, and the task tells them so.
    """

    name = "fix"

    def begin(self, root: str, state: Dict) -> None:
        state.setdefault("start_digest", "")
        state.setdefault("reproduced", False)
        state.setdefault("had_tests", bool(test_files(root)))

    def on_gate(self, state: Dict, digest: str, ok: bool) -> None:
        if not state.get("start_digest"):
            state["start_digest"] = digest
            # Red on arrival is reproduction only if there were tests to be
            # red — a gate failing because nothing ran proves nothing.
            if not ok and state.get("had_tests"):
                state["reproduced"] = True
            return
        if not ok:
            state["reproduced"] = True

    def veto(self, root: str, state: Dict) -> Optional[str]:
        if state.get("reproduced"):
            return None
        return (
            "No state in this session has made the gate fail, so the bug was never "
            "reproduced — and a fix for a bug nobody reproduced is a guess that "
            "happens to leave the tests green. Write a test that fails on the unfixed "
            "code, let the gate go red on it, then make it pass. Both sign-offs are "
            "cleared until then."
        )


class Add(Workflow):
    """A feature counts only once a test exercises it.

    Checks: at least one test file was added, or an existing one changed.

    Cannot check: that the new test exercises the new feature rather than
    something else. The reviewer is told to check that.
    """

    name = "add"

    def begin(self, root: str, state: Dict) -> None:
        state.setdefault("baseline_tests", test_files(root))

    def veto(self, root: str, state: Dict) -> Optional[str]:
        if test_files(root) != state.get("baseline_tests", {}):
            return None
        return (
            "No test was added or extended. A feature nothing tests is a claim the "
            "gate cannot check, which makes it a claim neither of you has verified. "
            "Add a test that fails without the feature. Both sign-offs are cleared "
            "until then."
        )


class Refactor(Workflow):
    """Behaviour must not change, and the tests are the definition of behaviour.

    Checks: no test file that existed at the start was edited or deleted. New
    test files are fine. Together with a gate that must be green to sign off,
    that is "same behaviour, measured by the same yardstick".

    Refused before it starts if the suite is already red: there is no way to
    show behaviour was preserved against tests that do not pass.

    Cannot check: behaviour the tests do not cover. A refactor that changes
    something untested passes this. It says so in the task.
    """

    name = "refactor"

    def begin(self, root: str, state: Dict) -> None:
        state.setdefault("baseline_tests", test_files(root))

    def veto(self, root: str, state: Dict) -> Optional[str]:
        before = state.get("baseline_tests", {})
        now = test_files(root)
        changed = sorted(p for p, h in before.items() if now.get(p) != h)
        if not changed:
            return None
        shown = ", ".join(changed[:6]) + (" and %d more" % (len(changed) - 6) if len(changed) > 6 else "")
        return (
            "Existing tests were edited or deleted: %s. In a refactor the existing tests "
            "are the definition of the behaviour being preserved — changing them moves "
            "the goalposts, so a green gate would prove nothing. Restore them exactly. "
            "New test files are fine. Both sign-offs are cleared until then." % shown
        )


class Plan(Workflow):
    """A plan, argued over, and nothing else.

    Checks: PLAN.md exists and has content, and no other file changed.
    """

    name = "plan"
    needs_gate = False
    OUTPUT = "PLAN.md"

    def begin(self, root: str, state: Dict) -> None:
        state.setdefault("baseline_files", all_files(root))

    def veto(self, root: str, state: Dict) -> Optional[str]:
        before = dict(state.get("baseline_files", {}))
        now = all_files(root)
        before.pop(self.OUTPUT, None)
        now_plan = now.pop(self.OUTPUT, None)
        touched = sorted(set(before) ^ set(now) | {p for p in before if p in now and before[p] != now[p]})
        problems: List[str] = []
        if touched:
            problems.append(
                "files other than %s changed: %s — this is a planning session, so "
                "revert them" % (self.OUTPUT, ", ".join(touched[:6]))
            )
        plan = Path(root).expanduser().resolve() / self.OUTPUT
        if now_plan is None or not plan.read_text(encoding="utf-8", errors="replace").strip():
            problems.append("%s does not exist or is empty" % self.OUTPUT)
        if not problems:
            return None
        return "Not a plan yet: " + "; ".join(problems) + ". Both sign-offs are cleared until then."


REGISTRY = {cls.name: cls for cls in (Fix, Add, Refactor, Plan)}


def get(name: str) -> Optional[Workflow]:
    cls = REGISTRY.get(name or "")
    return cls() if cls else None

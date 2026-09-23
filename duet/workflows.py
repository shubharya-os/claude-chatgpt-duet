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
import shutil
import subprocess
import tempfile
import uuid
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


# Directories a project needs in order to run its tests but that are not part
# of what anyone wrote. A replay links to them rather than copying them.
DEPENDENCY_DIRS = ("node_modules", ".venv", "venv")


def snapshot(root: str, state: Dict) -> None:
    """Copy the workspace as it was at the start, once per session.

    Kept under .duet, which the digest excludes, in a directory named for this
    session — a second `duet fix` in the same workspace must not replay
    against the first one's baseline. On resume the directory already exists
    and is kept, for the same reason `begin` keeps its baselines.
    """
    base = Path(root).expanduser().resolve()
    existing = state.get("baseline_dir")
    if existing and (base / existing).is_dir():
        return
    rel = ".duet/baselines/%s" % uuid.uuid4().hex[:12]
    dest = base / rel
    dest.mkdir(parents=True, exist_ok=True)
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        r = path.relative_to(base)
        if SKIP_DIRS & set(r.parts):
            continue
        target = dest / r
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(str(path), str(target))
        except OSError:
            continue
    state["baseline_dir"] = rel


def replay_fails(root: str, state: Dict) -> Optional[bool]:
    """Run today's tests against the code as it was at the start.

    True: the tests fail on the original code, so they detect the difference.
    False: they pass there too, so whatever they test, it is not this change.
    None: the replay could not be run, and the caller should say so rather
    than pretend it passed.

    This is a stronger question than "did the gate ever go red": a red caused
    by a typo in round 2 satisfies that, and a pair that writes the test and
    the fix in one turn never shows the harness a red at all — which is what
    the first live `duet fix` did.
    """
    base = Path(root).expanduser().resolve()
    gate = state.get("gate") or ""
    snap = base / state.get("baseline_dir", "")
    if not gate or not state.get("baseline_dir") or not snap.is_dir():
        return None
    # A replay asks whether the tests tell the two versions apart. With no
    # tests there is nothing to ask it about, and the gate failing on the old
    # code would say something about the gate, not about any test.
    if not test_files(root):
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="duet-replay-") as tmp:
            work = Path(tmp) / "w"
            shutil.copytree(str(snap), str(work))
            for rel in test_files(root):
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(base / rel), str(target))
            for name in DEPENDENCY_DIRS:
                if (base / name).is_dir() and not (work / name).exists():
                    (work / name).symlink_to(base / name)
            proc = subprocess.run(gate, cwd=str(work), shell=True,
                                  capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError, shutil.Error):
        return None
    return proc.returncode != 0


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
    """A fix counts only if its tests catch the bug in the original code.

    Checks: today's tests, run against the code as it was when the session
    started, FAIL there — so they detect the difference the fix makes — and
    pass on the fixed code (the gate, as for any sign-off). No test that
    existed at the start may have been deleted.

    This is replayed at sign-off, not inferred from history. The first
    version asked instead whether the gate had ever gone red, and the first
    live run showed both weaknesses at once: the pair wrote the test and the
    fix in one turn, so the harness never saw red, and after the veto one of
    them re-broke the code on purpose to show it. That satisfies a history
    check. It proves nothing a replay would not prove better.

    Cannot check: that the failure on the original code is *this* bug. A test
    that imports a helper the fix introduced fails there on the import, not
    on the bug. The reviewer is told to check exactly that.
    """

    name = "fix"

    def begin(self, root: str, state: Dict) -> None:
        state.setdefault("baseline_tests", test_files(root))
        state.setdefault("reproduced", False)
        state.setdefault("had_tests", bool(state["baseline_tests"]))
        snapshot(root, state)

    def on_gate(self, state: Dict, digest: str, ok: bool) -> None:
        # Kept as a fallback for when a replay cannot run — see veto().
        if not state.get("start_digest"):
            state["start_digest"] = digest
            if not ok and state.get("had_tests"):
                state["reproduced"] = True
            return
        if not ok:
            state["reproduced"] = True

    def veto(self, root: str, state: Dict) -> Optional[str]:
        gone = sorted(set(state.get("baseline_tests", {})) - set(test_files(root)))
        if gone:
            return (
                "Tests that existed at the start were deleted: %s. A fix makes a failing "
                "test pass; it does not remove it. Restore them. Both sign-offs are "
                "cleared until then." % ", ".join(gone[:6])
            )
        replay = replay_fails(root, state)
        state["replay"] = {True: "fails on original", False: "passes on original",
                           None: "could not run"}[replay]
        if replay is True:
            return None
        if replay is False:
            return (
                "Run against the original, unfixed code, the tests you signed off with "
                "all PASS — so none of them detects this bug, and the green gate says "
                "nothing about whether it is fixed. Add a test that fails on the code as "
                "it was and passes now. You do not need to re-break anything to show it: "
                "the harness replays your tests against the original itself. Both "
                "sign-offs are cleared until then."
            )
        if state.get("reproduced"):
            return None
        return (
            "The harness could not replay your tests against the original code, and "
            "no state in this session made the gate fail — so there is no evidence "
            "the bug was ever reproduced. Leave a failing test in place for one turn "
            "so the gate can see it red, then fix it. Both sign-offs are cleared "
            "until then."
        )


class Add(Workflow):
    """A feature counts only if a test fails without it.

    Checks: a test file was added or changed, and today's tests fail against
    the code as it was at the start — so something tests behaviour the
    original did not have. "A test file changed" on its own is satisfied by a
    whitespace edit.

    Cannot check: that the failing test exercises *this* feature rather than
    some other difference. The reviewer is told to check that.
    """

    name = "add"

    def begin(self, root: str, state: Dict) -> None:
        state.setdefault("baseline_tests", test_files(root))
        snapshot(root, state)

    def veto(self, root: str, state: Dict) -> Optional[str]:
        if test_files(root) == state.get("baseline_tests", {}):
            return (
                "No test was added or extended. A feature nothing tests is a claim the "
                "gate cannot check, which makes it a claim neither of you has verified. "
                "Add a test that fails without the feature. Both sign-offs are cleared "
                "until then."
            )
        replay = replay_fails(root, state)
        state["replay"] = {True: "fails on original", False: "passes on original",
                           None: "could not run"}[replay]
        if replay is False:
            return (
                "Run against the code as it was before this feature, your tests all "
                "still PASS — so nothing tests the feature itself; the tests you added "
                "would have passed without it. Add one that fails on the original code. "
                "Both sign-offs are cleared until then."
            )
        return None


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

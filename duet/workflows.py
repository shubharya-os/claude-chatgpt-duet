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

from duet.workspace import SKIP_DIRS as WORKSPACE_SKIP_DIRS

# What a test file looks like, across the ecosystems duet detects gates for.
TEST_GLOBS = (
    "test_*.py", "*_test.py", "*_test.go", "*.test.js", "*.test.ts",
    "*.test.jsx", "*.test.tsx", "*.spec.js", "*.spec.ts", "*_test.rb",
    "*_spec.rb", "*Test.java", "*Tests.java", "*_test.rs", "*Test.kt",
)
TEST_DIRS = ("tests", "test", "spec", "__tests__")
# The same list the workspace digest skips. Two lists drifted once: the plan rule
# counted `.pytest_cache` as a changed file, so a plan session whose agents ran
# the tests — which it now lets them do — was refused for it.
SKIP_DIRS = WORKSPACE_SKIP_DIRS


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
    failed = proc.returncode != 0
    state["replay_import_only"] = failed and failed_only_on_imports(
        (proc.stdout or "") + (proc.stderr or ""))
    return failed


# What a test run looks like when it failed because a test could not even be
# loaded, as opposed to because a check inside it failed.
IMPORT_SIGNS = ("ImportError while importing", "ModuleNotFoundError", "cannot import name",
                "ERROR collecting", "could not import ", "undefined: ", "cannot find value")
ASSERTION_SIGNS = ("AssertionError", "FAILED ", "FAIL: ", "--- FAIL", "assert ")


def failed_only_on_imports(output: str) -> bool:
    """Did the replay fail only because tests referenced code that did not exist yet?

    Then it proves the tests need the new code, not that they catch the bug —
    a test importing a helper the fix introduced fails on the original for that
    reason alone. Not a veto: pytest drops a whole module on one failed import,
    real checks inside it included, so blocking here would refuse legitimate
    fixes. It is said, so the person reading the result can check.
    """
    return (any(sign in output for sign in IMPORT_SIGNS)
            and not any(sign in output for sign in ASSERTION_SIGNS))


# How a test is named, per ecosystem — enough to list them, not to run them.
TEST_NAME_PATTERNS = (
    r"^\s*(?:async\s+)?def\s+(test_\w+)",            # Python
    r"^func\s+(Test\w+)",                               # Go
    r"\b(?:it|test)\(\s*['\"`]([^'\"`]{3,80})['\"`]",     # JS / TS
    r"#\[test\]\s*(?:async\s+)?fn\s+(\w+)",             # Rust
)
# Past this many, naming every test in a plan is busywork rather than care.
NAMED_TEST_LIMIT = 40


def test_names(root: str) -> List[str]:
    """The names of the tests that exist now, in file order, without duplicates."""
    import re
    base = Path(root).expanduser().resolve()
    names: List[str] = []
    for rel in sorted(test_files(root)):
        try:
            text = (base / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in TEST_NAME_PATTERNS:
            for match in re.finditer(pattern, text, re.MULTILINE):
                if match.group(1) not in names:
                    names.append(match.group(1))
    return names


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

    def proven(self, state: Dict) -> str:
        """What the check established, in a sentence — shown when the session ends.

        The rule is the reason to use a workflow at all, so the result of
        checking it belongs in front of the user, not only in state.json.
        """
        return ""


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
    on the bug. That case is detected and named in the verdict — not vetoed,
    since one bad import drops a whole pytest module, real checks included —
    and the reviewer is told to check it.
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


    def proven(self, state: Dict) -> str:
        if state.get("replay") == "fails on original" and state.get("replay_import_only"):
            return ("the tests fail on the original code, but only because they import code "
                    "the fix added — that proves they need the fix, not that they catch the "
                    "bug. Check that one exercises it through the original interface")
        if state.get("replay") == "fails on original":
            return "the tests fail on the original code and pass now, so they catch the bug"
        return "the gate failed during the session before it passed (no replay was possible)"


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


    def proven(self, state: Dict) -> str:
        if state.get("replay") == "fails on original":
            return "a test fails against the code as it was before, so the feature is tested"
        return "a test was added or extended (no replay was possible)"


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


    def proven(self, state: Dict) -> str:
        n = len(state.get("baseline_tests", {}))
        return ("all %d test file%s that existed at the start are byte-for-byte unchanged, and pass"
                % (n, "" if n == 1 else "s"))


class Plan(Workflow):
    """A plan, argued over, and nothing else.

    Checks: PLAN.md exists and has content, and no other file changed.
    """

    name = "plan"
    needs_gate = False
    OUTPUT = "PLAN.md"

    def begin(self, root: str, state: Dict) -> None:
        state.setdefault("baseline_files", all_files(root))
        state.setdefault("test_names", test_names(root))

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
        text = plan.read_text(encoding="utf-8", errors="replace") if now_plan is not None else ""
        if not text.strip():
            problems.append("%s does not exist or is empty" % self.OUTPUT)
        # The existing tests are the behaviour a change has to keep. A plan
        # that never walks through them is a plan for new code, not a plan for
        # changing this code — and in a head-to-head against another harness,
        # both plans missed a pitfall sitting in a parametrised test neither
        # had been made to read.
        names = state.get("test_names") or []
        if text.strip() and names:
            if len(names) <= NAMED_TEST_LIMIT:
                missing = [n for n in names if n not in text]
                if missing:
                    problems.append(
                        "these existing tests are not accounted for in %s: %s — for each, "
                        "say whether the plan keeps it passing and how"
                        % (self.OUTPUT, ", ".join(missing[:12]) + (" …" if len(missing) > 12 else "")))
            elif "existing test" not in text.lower():
                problems.append("%s has no section on the existing tests (%d of them)"
                                % (self.OUTPUT, len(names)))
        if not problems:
            return None
        return "Not a plan yet: " + "; ".join(problems) + ". Both sign-offs are cleared until then."


    def proven(self, state: Dict) -> str:
        n = len(state.get("test_names") or [])
        tests = (", and all %d existing tests are accounted for" % n) if 0 < n <= NAMED_TEST_LIMIT else ""
        return "only PLAN.md changed; no code was touched%s" % tests


REGISTRY = {cls.name: cls for cls in (Fix, Add, Refactor, Plan)}


def get(name: str) -> Optional[Workflow]:
    cls = REGISTRY.get(name or "")
    return cls() if cls else None

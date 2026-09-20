"""Starting a project from nothing.

A greenfield directory is where duet used to lose its own mechanism: no tests
exist, so gate detection finds nothing, and the session header reads "gate:
none — they can only agree by argument". That is also exactly where people
build fastest and ship worst.

So a build session sets the gate to the project's test command from round one,
before a single test exists. It is red until the pair writes real tests that
pass, and a red gate blocks both sign-offs. Having nothing to verify against
becomes the thing that stops them finishing, instead of the thing nobody
noticed.
"""

from __future__ import annotations

import shlex
import shutil
import sys
from pathlib import Path
from typing import Optional

from duet import ui

TASK = """\
Build this, from nothing, in this directory:

%(idea)s

Work in this order. Do not skip ahead.

1. AGREE WHAT DONE MEANS. Before any implementation, settle the acceptance
   criteria with your peer: concrete, checkable statements about behaviour,
   including the unhappy paths — bad input, a missing file, the empty case.
   Write them to ACCEPTANCE.md. Disagreeing now is cheap; after the code
   exists it is not.

2. WRITE THE TESTS FIRST, and watch them fail. Encode every criterion from
   ACCEPTANCE.md as a test. The gate already runs them, so it is red until
   they exist and pass, and neither of you can sign off while it is red. A
   test that cannot fail is worse than no test: it looks like proof and is
   not, so check each one fails before you make it pass.

3. THEN BUILD, until the gate is green.

4. CHECK WHAT YOU BUILT against the criteria you agreed in step 1, not
   against your memory of the task.

The ordinary rules still apply: answer every objection your peer raises, and
vote DONE only if you would ship exactly what is in the workspace now."""


# A gate for a directory with nothing in it yet. `unittest discover` alone is
# not safe here: on Python 3.11 and older it exits 0 when it finds no tests at
# all, so an empty workspace would look verified. This refuses to pass until
# at least one test has actually run.
EMPTY_SAFE_UNITTEST = (
    'import unittest as u,sys;'
    'r=u.TextTestRunner().run(u.defaultTestLoader.discover("."));'
    'print("" if r.testsRun else "no tests ran: this gate stays red until tests exist");'
    'sys.exit(0 if r.testsRun and r.wasSuccessful() else 1)'
)


# What to run when the project has no tests yet. Only a runner that is actually
# installed is offered: a gate that cannot start fails every turn and blocks
# consensus outright, which is worse than admitting there is no gate.
STARTERS = (
    ("package.json", "npm test"),
    ("Cargo.toml", "cargo test"),
    ("go.mod", "go test ./..."),
)


def starter_gate(root: str) -> Optional[str]:
    """A test command for a directory that has no tests yet.

    Mirrors the shape of the project when there is one to read, and otherwise
    assumes Python, because that is what duet can always reach: the interpreter
    running duet has pytest if duet was installed with it.
    """
    base = Path(root).expanduser().resolve()
    for marker, command in STARTERS:
        if (base / marker).is_file() and shutil.which(command.split()[0]):
            return command
    if shutil.which("pytest"):
        return "pytest -q"
    # unittest ships with Python, so this works on a machine with no test
    # runner installed at all. It gets its own zero-test guard: `unittest
    # discover` exits 0 when it finds nothing on Python 3.11 and older, and a
    # gate that passes an empty directory is the exact false proof this
    # command exists to prevent.
    return "%s -c %s" % (shlex.quote(sys.executable), shlex.quote(EMPTY_SAFE_UNITTEST))


def task_for(idea: str) -> str:
    return TASK % {"idea": idea.strip()}


def announce(gate: str, chosen: bool) -> None:
    """Say where the gate came from, before the session header prints it."""
    if not gate:
        print(ui.yellow("!  ") + "no gate: they can only agree by argument.")
        return
    if chosen:
        print(ui.dim("   gate  ") + gate)
        print(ui.dim("         nothing here to test yet, so this starts red — "
                     "and a red gate blocks both sign-offs."))


def run(args) -> int:
    """`duet build "<idea>"` — a run whose gate exists before the code does."""
    # Imported here, not at module scope: cli imports this module to register
    # the subcommand, so importing it back at the top is a cycle.
    from duet import cli

    # Before anything is printed: a nested session is refused outright, and a
    # gate line above that refusal would be noise about a session that is not
    # going to start.
    nested = cli.refuse_nested("build", args)
    if nested is not None:
        return nested

    idea = " ".join(getattr(args, "idea", None) or []).strip()
    if not idea:
        print(ui.red("no idea given."))
        print('usage: duet build "a CLI that renames photos by the date in their EXIF"')
        return 2

    root = str(Path(args.root).expanduser().resolve())
    configured = cli.load_config(root).gate
    chosen = False
    if args.gate is None and not args.no_gate and not configured:
        # detect() first: a project with tests already has a real gate, and a
        # starter would be a worse one. It only falls through on greenfield.
        args.gate = cli.gate_detect.detect(root) or starter_gate(root)
        chosen = True

    # cmd_run owns the parts that must not diverge between the two commands:
    # the nested-session guard, the unrunnable-gate check, sign-in preflight,
    # and the orchestrator itself. Only the task and the gate differ here.
    args.task = [task_for(idea)]
    args.file = None
    announce(args.gate or "", chosen)
    return cli.cmd_run(args)

"""Workflow rules, checked against what happened rather than what was said.

Every case here is a pair that agrees — both vote DONE on the same state with
the gate green — and the question is only whether that agreement is allowed
to count. A plain `duet run` would accept all of them.
"""

import json
import sys
from pathlib import Path

from duet import workflows
from duet.adapters.mock import MockAdapter, envelope
from duet.config import AgentSpec, Config
from duet.orchestrator import Orchestrator


def run(tmp_path, claude_script, gpt_script, **kwargs):
    cfg = Config(
        task="t", root=str(tmp_path),
        agents=[AgentSpec("claude", "mock"), AgentSpec("gpt", "mock")],
        start="claude", max_rounds=kwargs.pop("max_rounds", 8), **kwargs,
    )
    orch = Orchestrator(cfg, adapters={
        "claude": MockAdapter(name="claude", cwd=str(tmp_path), config={"script": claude_script}),
        "gpt": MockAdapter(name="gpt", cwd=str(tmp_path), config={"script": gpt_script}),
    })
    return orch, orch.run()


def kinds(result):
    path = Path(result.session_dir) / "events.jsonl"
    return [json.loads(l).get("kind") for l in path.read_text().splitlines() if l.strip()]


def write(path, content, verdict="DONE"):
    return envelope("wrote %s" % path, verdict, patches=[{"path": path, "content": content}])


DONE = envelope("I would ship this", "DONE", confidence=0.9)


# -- fix: the bug has to have been seen to fail --------------------------------

def test_a_fix_that_never_reproduced_the_bug_does_not_count(tmp_path):
    # The gate is red at the start only because nothing exists yet — no tests,
    # so no reproduction. The pair makes it green and both sign off.
    _, result = run(tmp_path, [write("fixed.txt", "yes")] + [DONE] * 6, [DONE] * 6,
                    workflow="fix", gate="test -f fixed.txt")
    assert result.status != "consensus"
    assert "workflow_veto" in kinds(result)


def test_a_fix_whose_bug_went_red_first_counts(tmp_path):
    gate = 'test "$(cat state.txt 2>/dev/null)" != red'
    _, result = run(
        tmp_path,
        [write("state.txt", "red", "CONTINUE"), DONE, DONE],   # reproduce
        [write("state.txt", "green"), DONE, DONE],              # then fix
        workflow="fix", gate=gate,
    )
    assert result.status == "consensus"
    assert "workflow_veto" not in kinds(result)


def test_a_suite_already_failing_on_arrival_counts_as_reproduced(tmp_path):
    (tmp_path / "test_bug.py").write_text("def test_bug():\n    assert False\n")
    _, result = run(tmp_path, [write("fixed.txt", "yes")] + [DONE] * 4, [DONE] * 4,
                    workflow="fix", gate="test -f fixed.txt")
    assert result.status == "consensus"


# -- add: something has to test it ---------------------------------------------

def test_a_feature_with_no_test_does_not_count(tmp_path):
    _, result = run(tmp_path, [write("feature.py", "x = 1\n")] + [DONE] * 6, [DONE] * 6,
                    workflow="add", gate="true")
    assert result.status != "consensus"
    assert "workflow_veto" in kinds(result)


PYTEST = "%s -m pytest -q -p no:cacheprovider" % sys.executable


def test_a_feature_with_a_test_that_fails_without_it_counts(tmp_path):
    feature = envelope("added double() and its test", "DONE", patches=[
        {"path": "feature.py", "content": "def double(x):\n    return 2 * x\n"},
        {"path": "test_feature.py",
         "content": "from feature import double\n\ndef test_double():\n    assert double(2) == 4\n"},
    ])
    _, result = run(tmp_path, [feature] + [DONE] * 3, [DONE] * 3, workflow="add", gate=PYTEST)
    assert result.status == "consensus"


def test_a_feature_whose_new_test_would_pass_without_it_does_not_count(tmp_path):
    """The whitespace-edit loophole: a test changed, but tests nothing new."""
    (tmp_path / "core.py").write_text("def one():\n    return 1\n")
    (tmp_path / "test_core.py").write_text("from core import one\n\ndef test_one():\n    assert one() == 1\n")
    unrelated = envelope("added a feature and a test", "DONE", patches=[
        {"path": "feature.py", "content": "def two():\n    return 2\n"},
        {"path": "test_extra.py",   # passes on the original code too
         "content": "from core import one\n\ndef test_one_again():\n    assert one() == 1\n"},
    ])
    _, result = run(tmp_path, [unrelated] + [DONE] * 6, [DONE] * 6, workflow="add", gate=PYTEST)
    assert result.status != "consensus"
    assert "workflow_veto" in kinds(result)


# -- fix, replayed: the tests have to catch the bug in the original ----------

BUGGY = 'def slugify(text):\n    return "-".join(text.lower().split())\n'
FIXED = ('import re\n\ndef slugify(text):\n'
         '    return "-".join(re.sub(r"[^\\w\\s-]", "", text.lower()).split())\n')
OLD_TEST = 'from slug import slugify\n\ndef test_spaces():\n    assert slugify("a b") == "a-b"\n'


def test_a_fix_written_in_one_turn_counts_without_re_breaking_anything(tmp_path):
    """What the first live `duet fix` did: test and fix together, gate never red.

    The history check vetoed it, and one agent then re-broke working code on
    purpose to show the harness a red gate. The replay accepts it outright,
    because the new test fails against the original code by itself.
    """
    (tmp_path / "slug.py").write_text(BUGGY)
    (tmp_path / "test_slug.py").write_text(OLD_TEST)
    both = envelope("fixed, with a test", "DONE", patches=[
        {"path": "slug.py", "content": FIXED},
        {"path": "test_slug.py", "content": OLD_TEST +
         '\ndef test_punctuation():\n    assert slugify("Hello, World!") == "hello-world"\n'},
    ])
    _, result = run(tmp_path, [both] + [DONE] * 3, [DONE] * 3, workflow="fix", gate=PYTEST)
    assert result.status == "consensus"
    assert "workflow_veto" not in kinds(result)


def test_a_fix_whose_tests_pass_on_the_buggy_code_does_not_count(tmp_path):
    (tmp_path / "slug.py").write_text(BUGGY)
    (tmp_path / "test_slug.py").write_text(OLD_TEST)
    blind = envelope("fixed, with a test that does not look at the bug", "DONE", patches=[
        {"path": "slug.py", "content": FIXED},
        {"path": "test_slug.py", "content": OLD_TEST +
         '\ndef test_lowercase():\n    assert slugify("ABC") == "abc"\n'},
    ])
    _, result = run(tmp_path, [blind] + [DONE] * 6, [DONE] * 6, workflow="fix", gate=PYTEST)
    assert result.status != "consensus"
    assert "workflow_veto" in kinds(result)


def test_a_fix_may_not_delete_the_failing_test(tmp_path):
    (tmp_path / "slug.py").write_text(BUGGY)
    (tmp_path / "test_bug.py").write_text(
        'from slug import slugify\n\ndef test_bug():\n    assert slugify("a,b") == "ab"\n')
    delete = envelope("removed the failing test", "DONE",
                      patches=[{"path": "test_bug.py", "action": "delete"}])
    _, result = run(tmp_path, [delete] + [DONE] * 6, [DONE] * 6, workflow="fix", gate=PYTEST)
    assert result.status != "consensus"


# -- refactor: the yardstick may not move --------------------------------------

def test_a_refactor_that_edits_an_existing_test_does_not_count(tmp_path):
    (tmp_path / "test_core.py").write_text("def test_core():\n    assert 1 + 1 == 2\n")
    _, result = run(tmp_path,
                    [write("test_core.py", "def test_core():\n    pass\n")] + [DONE] * 6,
                    [DONE] * 6, workflow="refactor", gate="true")
    assert result.status != "consensus"
    assert "workflow_veto" in kinds(result)


def test_a_refactor_that_leaves_the_tests_alone_counts(tmp_path):
    (tmp_path / "test_core.py").write_text("def test_core():\n    assert 1 + 1 == 2\n")
    _, result = run(tmp_path, [write("core.py", "def add(a, b):\n    return a + b\n")] + [DONE] * 3,
                    [DONE] * 3, workflow="refactor", gate="true")
    assert result.status == "consensus"


def test_a_refactor_may_add_tests(tmp_path):
    (tmp_path / "test_core.py").write_text("def test_core():\n    assert True\n")
    _, result = run(tmp_path, [write("test_extra.py", "def test_more():\n    pass\n")] + [DONE] * 3,
                    [DONE] * 3, workflow="refactor", gate="true")
    assert result.status == "consensus"


# -- plan: nothing but the plan ------------------------------------------------

def test_a_plan_that_also_changed_code_does_not_count(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    both = envelope("planned, and started", "DONE",
                    patches=[{"path": "PLAN.md", "content": "# plan\n1. do it\n"},
                             {"path": "app.py", "content": "x = 2\n"}])
    _, result = run(tmp_path, [both] + [DONE] * 6, [DONE] * 6, workflow="plan")
    assert result.status != "consensus"
    assert "workflow_veto" in kinds(result)


def test_a_plan_with_no_plan_in_it_does_not_count(tmp_path):
    _, result = run(tmp_path, [DONE] * 6, [DONE] * 6, workflow="plan")
    assert result.status != "consensus"


def test_a_plan_that_is_only_a_plan_counts(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    _, result = run(tmp_path, [write("PLAN.md", "# plan\n1. do it\n")] + [DONE] * 3, [DONE] * 3,
                    workflow="plan")
    assert result.status == "consensus"


# -- mechanics -----------------------------------------------------------------

def test_a_plain_run_is_unaffected(tmp_path):
    _, result = run(tmp_path, [write("feature.py", "x = 1\n"), DONE], [DONE, DONE], gate="true")
    assert result.status == "consensus"


def test_a_resumed_session_keeps_its_original_baseline():
    """begin() must not retake the baseline from wherever a session died."""
    wf = workflows.get("refactor")
    state = {"baseline_tests": {"test_core.py": "original-hash"}}
    wf.begin("/nonexistent", state)
    assert state["baseline_tests"] == {"test_core.py": "original-hash"}


def test_test_files_ignore_dependency_trees(tmp_path):
    (tmp_path / "test_mine.py").write_text("x")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "a.test.js").write_text("x")
    assert list(workflows.test_files(str(tmp_path))) == ["test_mine.py"]


# -- the commands: refusing to start when the rule could not be checked --------

import argparse  # noqa: E402

from duet import workflow_commands  # noqa: E402


def args_for(tmp_path, what="the thing", **kw):
    base = dict(what=[what], root=str(tmp_path), gate=None, no_gate=False,
                allow_nested=False, json=False, command="")
    base.update(kw)
    return argparse.Namespace(**base)


def test_fix_refuses_to_start_without_a_test_command(tmp_path, capsys):
    """No starter gate here: red because nothing exists would count as reproduced."""
    assert workflow_commands.run(args_for(tmp_path), "fix") == 3
    assert "needs this project's test command" in capsys.readouterr().out


def test_refactor_refuses_to_start_on_a_failing_suite(tmp_path, capsys):
    assert workflow_commands.run(args_for(tmp_path, gate="false"), "refactor") == 3
    out = capsys.readouterr().out
    assert "failing before the refactor has started" in out
    assert "duet fix" in out     # and says what to do instead


def test_every_workflow_refuses_an_empty_description(tmp_path, capsys):
    for name in ("fix", "add", "refactor", "plan"):
        assert workflow_commands.run(args_for(tmp_path, what=""), name) == 2
        assert "usage: duet %s" % name in capsys.readouterr().out


def test_each_task_names_its_gate_and_states_its_rule():
    for name in ("fix", "add", "refactor"):
        task = workflow_commands.TASKS[name] % {"what": "x", "gate": "pytest -q"}
        assert "pytest -q" in task, name
        assert "checked by the harness" in task, name
    plan = workflow_commands.TASKS["plan"] % {"what": "x", "gate": ""}
    assert "Only PLAN.md may change" in plan

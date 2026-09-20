"""Starting from nothing, without a gate that passes nothing.

The greenfield case is where duet's own mechanism used to lapse: no tests
exist, so nothing is detected, and the pair can only agree by argument. The
risk in fixing that is worse than the gap — a starter gate that goes green on
an empty directory would be a false proof, which is exactly what the double
sign-off is meant to rule out. So the cases here are mostly about staying red.
"""

import argparse
import subprocess

import pytest

from duet import build


def run_gate(command, cwd):
    proc = subprocess.run(command, shell=True, cwd=str(cwd),
                          capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout + proc.stderr


@pytest.fixture
def no_pytest(monkeypatch):
    """A machine with no test runner on PATH — the fallback's own case."""
    monkeypatch.setattr("duet.build.shutil.which", lambda name: None)


def test_greenfield_gate_is_red_before_any_test_exists(tmp_path, no_pytest):
    code, output = run_gate(build.starter_gate(str(tmp_path)), tmp_path)
    assert code != 0, "an empty directory must not pass its own gate"
    assert "no tests ran" in output


def test_the_same_gate_goes_green_on_a_real_test(tmp_path, no_pytest):
    (tmp_path / "test_thing.py").write_text(
        "import unittest\n"
        "class T(unittest.TestCase):\n"
        "    def test_ok(self):\n"
        "        self.assertEqual(1, 1)\n"
    )
    code, _ = run_gate(build.starter_gate(str(tmp_path)), tmp_path)
    assert code == 0


def test_a_failing_test_keeps_it_red(tmp_path, no_pytest):
    (tmp_path / "test_thing.py").write_text(
        "import unittest\n"
        "class T(unittest.TestCase):\n"
        "    def test_no(self):\n"
        "        self.assertEqual(1, 2)\n"
    )
    code, _ = run_gate(build.starter_gate(str(tmp_path)), tmp_path)
    assert code != 0


def test_the_fallback_survives_a_python_path_with_spaces(tmp_path, no_pytest, monkeypatch):
    monkeypatch.setattr("duet.build.sys.executable", "/opt/some where/python3")
    command = build.starter_gate(str(tmp_path))
    assert "'/opt/some where/python3'" in command


def test_an_existing_project_shape_wins_over_the_python_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("duet.build.shutil.which", lambda name: "/usr/bin/" + name)
    (tmp_path / "package.json").write_text("{}")
    assert build.starter_gate(str(tmp_path)) == "npm test"


def test_a_starter_is_not_offered_for_a_runner_that_is_not_installed(tmp_path, no_pytest):
    (tmp_path / "Cargo.toml").write_text("[package]\n")
    # No cargo on this machine, so a `cargo test` gate would fail every round
    # and veto consensus outright. The fallback has to be something runnable.
    assert "cargo" not in build.starter_gate(str(tmp_path))


def test_pytest_is_preferred_when_it_is_there(tmp_path, monkeypatch):
    monkeypatch.setattr("duet.build.shutil.which",
                        lambda name: "/usr/bin/pytest" if name == "pytest" else None)
    assert build.starter_gate(str(tmp_path)) == "pytest -q"


def test_the_task_puts_the_criteria_before_the_code(tmp_path):
    task = build.task_for("  a URL shortener  ")
    assert "a URL shortener" in task
    assert task.index("AGREE WHAT DONE MEANS") < task.index("THEN BUILD")
    assert task.index("WRITE THE TESTS FIRST") < task.index("THEN BUILD")


def test_build_refuses_to_nest_and_names_its_own_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DUET_SESSION", "20250101-000000-aaaa")
    args = argparse.Namespace(
        command="build", idea=["a thing"], root=str(tmp_path), gate=None,
        no_gate=False, allow_nested=False,
    )
    assert build.run(args) == 4
    out = capsys.readouterr().out
    # Telling a `duet build` user to retry with `duet run` would name a command
    # that does something else.
    assert "duet build --allow-nested" in out
    assert "gate" not in out.lower()


def test_an_empty_idea_is_refused_before_any_agent_is_started(tmp_path, capsys):
    args = argparse.Namespace(
        command="build", idea=[], root=str(tmp_path), gate=None,
        no_gate=False, allow_nested=False,
    )
    assert build.run(args) == 2
    assert "no idea given" in capsys.readouterr().out


def test_the_gate_line_does_not_claim_an_empty_workspace_when_tests_exist(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "test_thing.py").write_text("def test_ok():\n    assert True\n")
    build.announce("pytest -q", "detected")
    out = capsys.readouterr().out
    assert "nothing here to test yet" not in out
    assert "own test command" in out


def test_the_gate_line_says_so_on_a_real_greenfield(capsys):
    build.announce("pytest -q", "starter")
    assert "nothing here to test yet" in capsys.readouterr().out


def test_no_gate_is_stated_plainly_rather_than_left_silent(capsys):
    build.announce("", "yours")
    assert "no gate" in capsys.readouterr().out

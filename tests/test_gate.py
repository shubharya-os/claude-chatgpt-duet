"""Finding the test command, without inventing one.

A wrong gate that always passes is worse than no gate: it looks like
verification and is not. So every case here is either a real signal or a None.
"""

import json

import pytest

from duet.gate import describe, detect


@pytest.fixture
def tools_present(monkeypatch):
    """Pretend every tool is installed.

    These cases test the mapping from project marker to command. Whether the
    command can actually start is a separate concern, covered below — and a
    machine without cargo or maven should not fail the mapping tests.
    """
    monkeypatch.setattr("duet.gate.shutil.which", lambda name: "/usr/bin/" + name)


def test_a_python_project_with_tests(tmp_path, tools_present):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tests").mkdir()
    assert detect(str(tmp_path)) == "pytest -q"


def test_pyproject_without_any_tests_is_not_enough(tmp_path, tools_present):
    """A pyproject with no tests would give a gate that passes vacuously."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert detect(str(tmp_path)) is None


def test_loose_test_files_count(tmp_path, tools_present):
    (tmp_path / "test_thing.py").write_text("def test_ok():\n    assert True\n")
    assert detect(str(tmp_path)) == "pytest -q"


def test_npm_test_script(tmp_path, tools_present):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    assert detect(str(tmp_path)) == "npm test"


def test_the_npm_placeholder_script_is_ignored(tmp_path, tools_present):
    """`npm init` writes a test script that only prints an error and exits 1 —
    taking it would gate every session on a guaranteed failure."""
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}))
    assert detect(str(tmp_path)) is None


def test_npm_falls_back_to_check_then_ci(tmp_path, tools_present):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"check": "tsc --noEmit"}}))
    assert detect(str(tmp_path)) == "npm run check"


def test_a_makefile_needs_an_actual_test_target(tmp_path, tools_present):
    (tmp_path / "Makefile").write_text("build:\n\tgcc main.c\n")
    assert detect(str(tmp_path)) is None
    (tmp_path / "Makefile").write_text("build:\n\tgcc main.c\n\ntest:\n\t./run_tests\n")
    assert detect(str(tmp_path)) == "make test"


def test_other_ecosystems(tmp_path, tools_present):
    for marker, expected in [("Cargo.toml", "cargo test"), ("go.mod", "go test ./..."),
                             ("mix.exs", "mix test"), ("pom.xml", "mvn -q test")]:
        root = tmp_path / marker.replace(".", "_")
        root.mkdir()
        (root / marker).write_text("x")
        assert detect(str(root)) == expected


def test_an_empty_directory_gets_nothing(tmp_path):
    assert detect(str(tmp_path)) is None
    assert "no test command found" in describe(None)


def test_a_broken_package_json_does_not_raise(tmp_path):
    (tmp_path / "package.json").write_text("{not json")
    assert detect(str(tmp_path)) is None


# --- a gate that cannot start is worse than no gate -------------------------

def test_a_detected_gate_that_cannot_start_is_not_returned(tmp_path, monkeypatch):
    """Both agents caught this in a live session: `pytest -q` was detected on a
    machine where pytest lives only inside a virtualenv, so the gate failed
    every turn and no session could ever reach consensus, however right the
    pair were. Worse than no gate, because it blocks the finish."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")

    monkeypatch.setattr("duet.gate.shutil.which", lambda name: None)
    monkeypatch.setattr("duet.gate._python_module_form", lambda command: None)
    assert detect(str(tmp_path)) is None


def test_pytest_falls_back_to_the_interpreter_that_has_it(tmp_path, monkeypatch):
    """pytest is usually in a virtualenv rather than on PATH, and the python
    running duet is the one most likely to have it."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")

    monkeypatch.setattr("duet.gate.shutil.which", lambda name: None)
    found = detect(str(tmp_path))
    assert found is not None
    assert "-m pytest" in found and found.endswith("-q")


def test_a_relative_launcher_in_the_project_counts(tmp_path, monkeypatch):
    import os
    import stat

    (tmp_path / "build.gradle").write_text("x")
    launcher = tmp_path / "gradlew"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setattr("duet.gate.shutil.which", lambda name: None)
    assert detect(str(tmp_path)) == "./gradlew test"


def test_an_npm_script_is_dropped_when_npm_is_missing(tmp_path, monkeypatch):
    import json as _json

    (tmp_path / "package.json").write_text(_json.dumps({"scripts": {"test": "jest"}}))
    monkeypatch.setattr("duet.gate.shutil.which", lambda name: None)
    assert detect(str(tmp_path)) is None


def test_an_unrunnable_gate_is_refused_before_the_session_starts(tmp_path, capsys):
    """Discovered the expensive way: a session ran eight rounds, hit arbitration,
    and could never have finished, because its gate could not start and a failing
    gate vetoes both agents. Refusing takes a second."""
    from duet.cli import main

    code = main(["run", "do a thing", "-C", str(tmp_path), "--gate", "definitely-not-installed"])
    assert code == 3
    out = capsys.readouterr().out
    assert "cannot run" in out
    assert "could never finish" in out
    assert "--no-gate" in out                 # and both ways out are named
    assert "--gate" in out


def test_a_runnable_gate_is_not_refused(tmp_path, monkeypatch):
    from duet.gate import why_unusable

    assert why_unusable("echo hello", str(tmp_path)) is None
    assert why_unusable("", str(tmp_path)) is None
    assert why_unusable("CI=1 echo hello", str(tmp_path)) is None


def test_a_project_local_launcher_is_runnable(tmp_path):
    import stat

    from duet.gate import why_unusable

    launcher = tmp_path / "run-tests.sh"
    launcher.write_text("#!/bin/sh\nexit 0\n")
    launcher.chmod(launcher.stat().st_mode | stat.S_IEXEC)
    assert why_unusable("run-tests.sh", str(tmp_path)) is None
    assert why_unusable("missing.sh", str(tmp_path)) is not None

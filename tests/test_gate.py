"""Finding the test command, without inventing one.

A wrong gate that always passes is worse than no gate: it looks like
verification and is not. So every case here is either a real signal or a None.
"""

import json

from duet.gate import describe, detect


def test_a_python_project_with_tests(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "tests").mkdir()
    assert detect(str(tmp_path)) == "pytest -q"


def test_pyproject_without_any_tests_is_not_enough(tmp_path):
    """A pyproject with no tests would give a gate that passes vacuously."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert detect(str(tmp_path)) is None


def test_loose_test_files_count(tmp_path):
    (tmp_path / "test_thing.py").write_text("def test_ok():\n    assert True\n")
    assert detect(str(tmp_path)) == "pytest -q"


def test_npm_test_script(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}))
    assert detect(str(tmp_path)) == "npm test"


def test_the_npm_placeholder_script_is_ignored(tmp_path):
    """`npm init` writes a test script that only prints an error and exits 1 —
    taking it would gate every session on a guaranteed failure."""
    (tmp_path / "package.json").write_text(json.dumps(
        {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}))
    assert detect(str(tmp_path)) is None


def test_npm_falls_back_to_check_then_ci(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"check": "tsc --noEmit"}}))
    assert detect(str(tmp_path)) == "npm run check"


def test_a_makefile_needs_an_actual_test_target(tmp_path):
    (tmp_path / "Makefile").write_text("build:\n\tgcc main.c\n")
    assert detect(str(tmp_path)) is None
    (tmp_path / "Makefile").write_text("build:\n\tgcc main.c\n\ntest:\n\t./run_tests\n")
    assert detect(str(tmp_path)) == "make test"


def test_other_ecosystems(tmp_path):
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

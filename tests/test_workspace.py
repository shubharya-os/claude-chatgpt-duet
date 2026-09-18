import pytest

from duet.protocol import Patch
from duet.workspace import PatchRejected, Workspace


def test_digest_changes_with_content(tmp_path):
    ws = Workspace(str(tmp_path))
    before = ws.digest()
    (tmp_path / "a.txt").write_text("hello")
    assert ws.digest() != before
    stable = ws.digest()
    assert ws.digest() == stable  # deterministic


def test_digest_ignores_duet_internals(tmp_path):
    ws = Workspace(str(tmp_path))
    (tmp_path / "a.txt").write_text("hello")
    before = ws.digest()
    (tmp_path / ".duet" / "sessions").mkdir(parents=True)
    (tmp_path / ".duet" / "sessions" / "log").write_text("noise")
    assert ws.digest() == before


def test_patches_apply_and_report(tmp_path):
    ws = Workspace(str(tmp_path))
    log = ws.apply_patches([Patch(path="pkg/mod.py", action="write", content="x = 1\n")])
    assert (tmp_path / "pkg" / "mod.py").read_text() == "x = 1\n"
    assert "created" in log[0]
    assert "unchanged" in ws.apply_patches([Patch(path="pkg/mod.py", content="x = 1\n")])[0]
    assert "deleted" in ws.apply_patches([Patch(path="pkg/mod.py", action="delete")])[0]


@pytest.mark.parametrize("path", ["../escape.txt", "/etc/passwd", ".git/config", ".duet/config.json"])
def test_paths_outside_the_workspace_are_refused(tmp_path, path):
    ws = Workspace(str(tmp_path))
    with pytest.raises(PatchRejected):
        ws.resolve(path)
    log = ws.apply_patches([Patch(path=path, content="pwned")])
    assert log[0].startswith("REJECTED")


def test_gate_result_reflects_the_command(tmp_path):
    assert Workspace(str(tmp_path), gate="true").run_gate().ok
    failed = Workspace(str(tmp_path), gate="exit 3").run_gate()
    assert not failed.ok and failed.exit_code == 3
    assert Workspace(str(tmp_path)).run_gate().skipped

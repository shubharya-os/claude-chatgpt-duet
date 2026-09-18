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


def _git(tmp_path, *args):
    import subprocess
    return subprocess.run(["git", *args], cwd=str(tmp_path), capture_output=True, text=True)


def test_diff_shows_new_files_without_staging_them(tmp_path):
    """duet is a guest in someone's repo: it must not quietly stage their files
    just to render a diff for the reviewer."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "tracked.txt").write_text("one\n")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-qm", "initial")

    (tmp_path / "tracked.txt").write_text("two\n")
    (tmp_path / "brand_new.py").write_text("x = 1\n")

    ws = Workspace(str(tmp_path))
    out = ws.diff()
    assert "two" in out                       # the edit is visible
    assert "brand_new.py" in out              # and so is the new file

    staged = _git(tmp_path, "diff", "--cached", "--name-only").stdout
    assert "brand_new.py" not in staged       # but nothing was staged
    assert staged.strip() == ""


def test_diff_falls_back_to_a_listing_outside_a_repo(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    assert "a.py" in Workspace(str(tmp_path)).diff()


def test_a_large_file_edited_in_place_changes_the_state_id(tmp_path):
    """Summarising big files by length let two different workspaces share a
    state id, so a sign-off — and a cached gate result — carried across a change
    neither agent had seen."""
    big = tmp_path / "big.dat"
    big.write_bytes(b"OK" + b"\0" * 3_000_000)
    ws = Workspace(str(tmp_path))
    before = ws.digest()

    big.write_bytes(b"NO" + b"\0" * 3_000_000)   # same length, different content
    assert big.stat().st_size == 3_000_002
    assert ws.digest() != before


def test_retargeting_a_symlink_changes_the_state_id(tmp_path):
    (tmp_path / "a.txt").write_text("one")
    (tmp_path / "b.txt").write_text("two")
    link = tmp_path / "current.txt"
    link.symlink_to("a.txt")

    ws = Workspace(str(tmp_path))
    before = ws.digest()
    link.unlink()
    link.symlink_to("b.txt")
    assert ws.digest() != before


def test_a_symlink_is_not_followed_when_hashing(tmp_path):
    outside = tmp_path.parent / "outside-target.txt"
    outside.write_text("secret")
    (tmp_path / "link.txt").symlink_to(outside)
    ws = Workspace(str(tmp_path))
    assert ws.digest()            # does not raise, does not read through
    outside.unlink()

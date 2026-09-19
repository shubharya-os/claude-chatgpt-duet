"""The first five minutes, on a machine that is not this one.

Everything here is a failure that only shows up on someone else's laptop: a
home directory with a space in it, npm installing into a directory nobody's
PATH mentions, a second `duet setup` over a first one, and — the one this
project exists for — a command that prints a tick it has not earned.
"""

import stat
import subprocess
import sys
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# /duet is only installed if duet can actually be run


def test_skill_install_does_not_claim_duet_works_when_nothing_runs(tmp_path, monkeypatch, capsys):
    """Every candidate invocation failed, and it printed four green ticks and
    "In a new Claude Code session: /duet ..." and exited 0. Typing /duet then
    fails on a machine that was told it was ready. That is the whole failure
    class this project is about."""
    from duet.cli import main

    monkeypatch.setattr("duet.cli._invocation_works", lambda command: False)
    monkeypatch.setattr("duet.cli.shutil.which", lambda name: None)

    home = tmp_path / "home"
    assert main(["skill", "install", "--dir", str(home)]) == 1
    out = capsys.readouterr().out
    assert "/duet will not work yet" in out
    assert "export PATH=" in out                  # names the fix, not just the problem
    assert "duet skill install --force" in out
    # and it does not go on to say it is ready
    assert "In a new " not in out


def test_setup_fails_when_duet_cannot_be_invoked(tmp_path, monkeypatch, capsys):
    """`duet setup` ends with "done. Open a new Claude Code session and type
    /duet". It must not reach that line when /duet cannot call duet."""
    from duet.adapters.base import Probe
    from duet.cli import main

    for module in ("claude_code.ClaudeCodeAdapter", "codex_cli.CodexCliAdapter"):
        monkeypatch.setattr("duet.adapters.%s.probe" % module,
                            classmethod(lambda cls, config=None: Probe(ok=True, detail="signed in")))
    monkeypatch.setattr("duet.cli.Adapter.which", staticmethod(lambda *a: "/usr/bin/true"))
    monkeypatch.setattr("duet.cli._invocation_works", lambda command: False)
    monkeypatch.setattr("duet.cli.shutil.which", lambda name: None)
    monkeypatch.setattr("duet.cli.Path.home", staticmethod(lambda: tmp_path / "home"))

    assert main(["setup", "-C", str(tmp_path), "--yes"]) == 1
    assert "done." not in capsys.readouterr().out


def test_a_verified_invocation_still_reports_success(tmp_path, monkeypatch, capsys):
    """The honest failure must not fire when things are fine."""
    from duet.cli import main

    monkeypatch.setattr("duet.cli._invocation_works", lambda command: True)
    monkeypatch.setattr("duet.cli.shutil.which", lambda name: None)

    home = tmp_path / "home"
    assert main(["skill", "install", "--dir", str(home)]) == 0
    out = capsys.readouterr().out
    assert "In a new " in out
    assert "will not work yet" not in out


# --------------------------------------------------------------------------
# a home directory with a space in it


def test_the_invocation_survives_a_space_in_the_path(tmp_path, monkeypatch):
    """`/Users/Ada Lovelace` is an ordinary macOS home. Unquoted, the fallback
    invocation becomes `PYTHONPATH=/Users/Ada Lovelace/x python -m duet`, and
    the shell runs `Lovelace/x` as a command: "et: command not found"."""
    import shlex

    from duet.cli import _candidate_invocations

    spaced = tmp_path / "Ada Lovelace" / "bin"
    spaced.mkdir(parents=True)
    fake = spaced / "duet"
    fake.write_text("#!/bin/sh\necho 'duet 0.1.0'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setattr("duet.cli.shutil.which",
                        lambda name: str(fake) if name == "duet" else None)
    monkeypatch.setattr("duet.cli.sys.executable", str(fake))

    for candidate in _candidate_invocations():
        # Every candidate must survive being handed to a shell: the first word
        # after any VAR= prefix has to be the whole path, not its first word.
        words = shlex.split(candidate)
        program = next(w for w in words if "=" not in w.split("/")[0] or w.startswith("/"))
        assert Path(program).exists(), "%r splits into a path that is not there" % candidate


def test_a_spaced_path_invocation_actually_runs(tmp_path, monkeypatch):
    """The end-to-end version of the above: the chosen invocation is executed."""
    from duet.cli import _candidate_invocations, _invocation_works

    spaced = tmp_path / "Ada Lovelace"
    spaced.mkdir(parents=True)
    fake = spaced / "duet"
    fake.write_text("#!/bin/sh\necho 'duet 0.1.0'\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setattr("duet.cli.shutil.which",
                        lambda name: str(fake) if name == "duet" else None)
    assert _invocation_works(_candidate_invocations()[0])


# --------------------------------------------------------------------------
# a second run over a previous install


def test_reinstall_updates_a_file_duet_wrote_with_an_older_invocation(tmp_path, monkeypatch, capsys):
    """duet moves — a `pip --user` install replaced by a virtualenv, or `duet`
    finally reaching PATH. The files then hold the old invocation, and
    refusing them as "a different version is already there" failed a plain
    `duet setup` over a working install, for a file duet wrote itself."""
    from duet.cli import main

    monkeypatch.setattr("duet.cli._invocation_works", lambda command: True)
    home = tmp_path / "home"

    monkeypatch.setattr("duet.cli.shutil.which", lambda name: "/old/path/duet")
    assert main(["skill", "install", "--dir", str(home)]) == 0
    command = home / ".claude" / "commands" / "duet.md"
    assert "/old/path/duet" in command.read_text()
    capsys.readouterr()

    monkeypatch.setattr("duet.cli.shutil.which", lambda name: "/new/path/duet")
    assert main(["skill", "install", "--dir", str(home)]) == 0, capsys.readouterr().out
    text = command.read_text()
    assert "/new/path/duet" in text
    assert "/old/path/duet" not in text


def test_a_hand_edited_file_is_still_protected(tmp_path, monkeypatch, capsys):
    """The rule above must not turn into "overwrite whatever is there"."""
    from duet.cli import main

    monkeypatch.setattr("duet.cli._invocation_works", lambda command: True)
    monkeypatch.setattr("duet.cli.shutil.which", lambda name: "/old/path/duet")
    home = tmp_path / "home"
    assert main(["skill", "install", "--dir", str(home)]) == 0
    capsys.readouterr()

    command = home / ".claude" / "commands" / "duet.md"
    command.write_text(command.read_text() + "\n## my own section\nDo not lose this.\n")

    assert main(["skill", "install", "--dir", str(home)]) == 1
    out = capsys.readouterr().out
    assert "already there" in out
    assert "--force" in out
    assert "Do not lose this." in command.read_text()


# --------------------------------------------------------------------------
# npm installed them somewhere nobody's PATH mentions


def test_setup_names_the_npm_directory_when_the_clis_are_still_missing(tmp_path, monkeypatch, capsys):
    """npm exits 0 and `claude` is still not found: it installed into a prefix
    that is not on PATH. "Run it yourself and re-run setup" is the wrong
    instruction — running it again puts them in the same place."""
    from duet.cli import main

    monkeypatch.setattr("duet.cli.Adapter.which",
                        staticmethod(lambda name: "/usr/bin/npm" if name == "npm" else None))
    monkeypatch.setattr("duet.cli.subprocess.call", lambda *a, **k: 0)
    monkeypatch.setattr("duet.cli._npm_global_bin", lambda: "/opt/node/bin")
    monkeypatch.setattr("duet.cli.Path.home", staticmethod(lambda: tmp_path / "home"))

    assert main(["setup", "-C", str(tmp_path), "--yes"]) == 1
    out = capsys.readouterr().out
    assert "/opt/node/bin" in out
    assert 'export PATH="/opt/node/bin:$PATH"' in out
    assert "did not finish cleanly" not in out


def test_setup_says_how_to_find_the_npm_directory_when_npm_will_not_say(tmp_path, monkeypatch, capsys):
    from duet.cli import main

    monkeypatch.setattr("duet.cli.Adapter.which",
                        staticmethod(lambda name: "/usr/bin/npm" if name == "npm" else None))
    monkeypatch.setattr("duet.cli.subprocess.call", lambda *a, **k: 0)
    monkeypatch.setattr("duet.cli._npm_global_bin", lambda: None)
    monkeypatch.setattr("duet.cli.Path.home", staticmethod(lambda: tmp_path / "home"))

    assert main(["setup", "-C", str(tmp_path), "--yes"]) == 1
    assert "npm prefix -g" in capsys.readouterr().out


def test_npm_global_bin_falls_back_when_npm_bin_g_is_gone(monkeypatch):
    """`npm bin -g` was removed in npm 9 and errors there; `npm prefix -g` is
    the one that has always worked."""
    from duet import cli

    class Result:
        def __init__(self, code, out):
            self.returncode, self.stdout = code, out

    def run(argv, **kwargs):
        if argv[1] == "bin":
            return Result(1, "Unknown command: \"bin\"\n")
        return Result(0, "/opt/homebrew\n")

    monkeypatch.setattr("duet.cli.subprocess.run", run)
    assert cli._npm_global_bin() == str(Path("/opt/homebrew") / "bin")


def test_a_still_broken_npm_run_keeps_the_old_message(tmp_path, monkeypatch, capsys):
    """npm itself failing is a different problem and keeps its own advice."""
    from duet.cli import main

    monkeypatch.setattr("duet.cli.Adapter.which",
                        staticmethod(lambda name: "/usr/bin/npm" if name == "npm" else None))
    monkeypatch.setattr("duet.cli.subprocess.call", lambda *a, **k: 1)
    monkeypatch.setattr("duet.cli.Path.home", staticmethod(lambda: tmp_path / "home"))

    assert main(["setup", "-C", str(tmp_path), "--yes"]) == 1
    assert "did not finish cleanly" in capsys.readouterr().out

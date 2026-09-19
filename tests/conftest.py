import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _outside_a_duet_session(monkeypatch):
    """No test inherits the marker of a session it is running inside.

    `DUET_SESSION` is set in the environment of every agent duet runs, and this
    suite gets run from inside duet sessions. Left alone, every command that
    calls `refuse_nested` — `run`, `resume` — would refuse before doing anything,
    and the tests for them would only pass if some earlier test happened to run a
    session first and clear it. The tests that want the marker set it themselves.
    """
    monkeypatch.delenv("DUET_SESSION", raising=False)
    yield
    os.environ.pop("DUET_SESSION", None)


@pytest.fixture(autouse=True)
def _own_state_dir(tmp_path_factory, monkeypatch):
    """No test reads or writes the developer's real ~/.duet.

    duet remembers a Codex usage limit there. Left alone, a machine that had
    actually hit one would fail the probe tests, and a test that provokes one
    would leave that memory behind for the next real `duet doctor`.
    """
    monkeypatch.setenv("DUET_STATE_DIR", str(tmp_path_factory.mktemp("duet-state")))

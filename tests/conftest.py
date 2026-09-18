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

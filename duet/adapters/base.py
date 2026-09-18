from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AgentReply:
    text: str
    meta: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class Probe:
    """What `duet doctor` reports for one backend."""

    ok: bool
    detail: str
    fix: str = ""


class Adapter:
    """A peer that can take a turn.

    `edits_workspace` is the important one: an agent with its own tools changes
    files itself, and an agent without them ships `patches` in its envelope for
    the harness to apply. Either way both sides can build, so either side can
    lead.
    """

    backend = "base"
    display = "agent"
    edits_workspace = False

    def __init__(self, name: str = "agent", cwd: str = ".", model: str = "", config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.cwd = str(Path(cwd).expanduser().resolve())
        self.model = model
        self.config = dict(config or {})
        self.session_id: Optional[str] = None

    # -- interface --------------------------------------------------------
    def send(self, prompt: str, system: str = "", round_no: int = 0) -> AgentReply:
        raise NotImplementedError

    def allow_gate(self, gate: str) -> None:
        """Let this agent run the acceptance gate itself, if it needs permission.

        An agent that cannot run the gate is taking the harness's word for it,
        which is exactly the second-hand knowledge this project exists to avoid.
        Default: nothing to do.
        """
        return None

    @classmethod
    def probe(cls, config: Optional[Dict[str, Any]] = None) -> Probe:
        return Probe(ok=False, detail="no probe implemented for %s" % cls.backend)

    def describe(self) -> str:
        return "%s (%s%s)" % (self.display, self.backend, ", " + self.model if self.model else "")

    def state(self) -> Dict[str, Any]:
        return {"session_id": self.session_id}

    def restore(self, state: Dict[str, Any]) -> None:
        self.session_id = (state or {}).get("session_id")

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def which(*candidates: str) -> Optional[str]:
        for candidate in candidates:
            if not candidate:
                continue
            if os.path.sep in candidate:
                if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                    return candidate
                continue
            found = shutil.which(candidate)
            if found:
                return found
        return None

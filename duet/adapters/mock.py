"""A scripted peer.

`duet demo` and the test suite run the entire orchestrator — envelopes, issues,
arbitration, double signoff — with no API key and no network, so the loop can be
verified on its own terms.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from duet.adapters.base import Adapter, AgentReply, Probe


def envelope(message: str, verdict: str = "CONTINUE", **kwargs) -> str:
    body: Dict[str, Any] = {"message": message, "verdict": verdict}
    body.update(kwargs)
    return "%s\n\n```json\n%s\n```" % (message, json.dumps(body, indent=2))


class MockAdapter(Adapter):
    backend = "mock"
    display = "mock"
    edits_workspace = False

    def __init__(self, *args, script: Optional[List[str]] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.script: List[str] = list(script or self.config.get("script") or [])
        self.turns = 0
        self.prompts: List[str] = []

    def send(self, prompt: str, system: str = "", round_no: int = 0) -> AgentReply:
        self.prompts.append(prompt)
        if self.turns < len(self.script):
            text = self.script[self.turns]
        elif self.script:
            text = self.script[-1]
        else:
            text = envelope("mock %s has nothing to add" % self.name, "DONE", confidence=0.9)
        self.turns += 1
        return AgentReply(text=text, meta={"backend": "mock", "turn": self.turns})

    @classmethod
    def probe(cls, config: Optional[Dict[str, Any]] = None) -> Probe:
        return Probe(ok=True, detail="mock backend is always available")

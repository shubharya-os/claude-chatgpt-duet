"""ChatGPT with hands: OpenAI's Codex CLI.

Optional. If it is installed, the ChatGPT side edits the workspace directly
instead of shipping patches, which makes the two peers symmetrical.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional

from duet.adapters.base import Adapter, AgentReply, Probe

DEFAULT_CANDIDATES = (
    os.environ.get("DUET_CODEX_BIN", ""),
    "codex",
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
)


class CodexCliAdapter(Adapter):
    backend = "codex-cli"
    display = "ChatGPT (Codex CLI)"
    edits_workspace = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bin = self.which(*DEFAULT_CANDIDATES) or "codex"
        self.timeout = int(self.config.get("timeout", 1800))
        self.sandbox = str(self.config.get("sandbox", "workspace-write"))
        self.extra_args: List[str] = list(self.config.get("extra_args") or [])
        self.turns = 0

    def _base(self) -> List[str]:
        cmd = [self.bin, "exec", "--skip-git-repo-check"]
        if self.sandbox:
            cmd += ["--sandbox", self.sandbox]
        if self.model:
            cmd += ["--model", self.model]
        return cmd + self.extra_args

    def _run(self, args: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            args, cwd=self.cwd, capture_output=True, text=True, timeout=self.timeout
        )

    def send(self, prompt: str, system: str = "", round_no: int = 0) -> AgentReply:
        full = ("%s\n\n---\n\n%s" % (system, prompt)) if system else prompt
        attempts: List[List[str]] = []
        if self.turns:
            attempts.append(self._base() + ["resume", "--last", full])
        attempts.append(self._base() + [full])

        last_error = ""
        for args in attempts:
            try:
                proc = self._run(args)
            except FileNotFoundError:
                return AgentReply(
                    text="",
                    error="the `codex` CLI was not found. Install it with "
                    "`npm i -g @openai/codex`, or use the openai-api backend.",
                )
            except subprocess.TimeoutExpired:
                return AgentReply(text="", error="codex timed out after %ds" % self.timeout)
            text = (proc.stdout or "").strip()
            if proc.returncode == 0 and text:
                self.turns += 1
                return AgentReply(text=text, meta={"backend": self.backend, "model": self.model})
            last_error = ((proc.stderr or "") + "\n" + text).strip()[:800] or "codex exited %d" % proc.returncode
        return AgentReply(text="", error=last_error)

    @classmethod
    def probe(cls, config: Optional[Dict[str, Any]] = None) -> Probe:
        binary = Adapter.which(*DEFAULT_CANDIDATES)
        if not binary:
            return Probe(
                ok=False,
                detail="`codex` CLI not found (optional)",
                fix="npm i -g @openai/codex   — or stay on the openai-api backend",
            )
        try:
            proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            return Probe(ok=False, detail="found %s but could not run it: %s" % (binary, exc))
        if proc.returncode != 0:
            return Probe(ok=False, detail="%s --version exited %d" % (binary, proc.returncode))
        return Probe(ok=True, detail="%s (%s)" % ((proc.stdout or "").strip() or "installed", binary))

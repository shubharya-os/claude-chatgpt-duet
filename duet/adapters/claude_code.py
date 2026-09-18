"""Claude Code, headless.

Runs the real `claude` CLI in the workspace, so this side of the duet has
genuine tools: it reads, edits, runs commands and tests. Session continuity
comes from `--resume`, but every prompt the orchestrator builds is
self-contained, so losing the session id costs context, not correctness.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from duet.adapters.base import Adapter, AgentReply, Probe

DEFAULT_CANDIDATES = (
    os.environ.get("DUET_CLAUDE_BIN", ""),
    "claude",
    str(Path.home() / ".claude" / "local" / "claude"),
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
    str(Path.home() / ".local" / "bin" / "claude"),
)

INSTALL_HINT = "npm install -g @anthropic-ai/claude-code"
LOGIN_HINT = "claude auth login   (sign in with your Claude account — no API key needed)"


def read_auth_status(binary: str, timeout: int = 45) -> Tuple[Optional[bool], str]:
    """(logged_in, description). None means it could not be determined.

    `claude auth status` prints JSON and costs nothing, which is the only honest
    way to answer "is this connected?" — a successful `--version` says only that
    the binary exists, and that false green is exactly what sends someone into a
    session that fails on its first turn.
    """
    try:
        proc = subprocess.run(
            [binary, "auth", "status"], capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    try:
        start = out.index("{")
        data = json.loads(out[start : out.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        low = out.lower()
        if "not logged in" in low or "please run /login" in low:
            return False, "not logged in"
        return None, out.splitlines()[0] if out else "no output from `claude auth status`"
    if not isinstance(data, dict) or "loggedIn" not in data:
        return None, "unrecognised output from `claude auth status`"
    method = str(data.get("authMethod") or "unknown")
    if data.get("loggedIn"):
        return True, "signed in (%s)" % method
    return False, "not signed in"


class ClaudeCodeAdapter(Adapter):
    backend = "claude-code"
    display = "Claude Code"
    edits_workspace = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bin = self.which(*DEFAULT_CANDIDATES) or "claude"
        self.timeout = int(self.config.get("timeout", 1800))
        self.permission_mode = self.config.get("permission_mode", "acceptEdits")
        self.extra_args: List[str] = list(self.config.get("extra_args") or [])

    def _command(self, prompt: str, system: str) -> List[str]:
        cmd = [self.bin, "-p", prompt, "--output-format", "json"]
        if self.permission_mode == "bypass":
            cmd.append("--dangerously-skip-permissions")
        elif self.permission_mode:
            cmd += ["--permission-mode", self.permission_mode]
        if self.model:
            cmd += ["--model", self.model]
        if system:
            cmd += ["--append-system-prompt", system]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        cmd += self.extra_args
        return cmd

    @staticmethod
    def _extract(payload: Any) -> Dict[str, Any]:
        """Tolerate both the single result object and a list of events."""
        if isinstance(payload, list):
            for item in reversed(payload):
                if isinstance(item, dict) and item.get("type") == "result":
                    return item
            return payload[-1] if payload and isinstance(payload[-1], dict) else {}
        return payload if isinstance(payload, dict) else {}

    def send(self, prompt: str, system: str = "", round_no: int = 0) -> AgentReply:
        cmd = self._command(prompt, system)
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                # the prompt is passed with -p; an inherited stdin pipe would
                # only give the CLI something else to wait on
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return AgentReply(
                text="",
                error="the `claude` CLI was not found. Install it with "
                "`%s`, then `claude auth login` to sign in. "
                "You can also point duet at it with DUET_CLAUDE_BIN." % INSTALL_HINT,
            )
        except subprocess.TimeoutExpired:
            return AgentReply(text="", error="claude timed out after %ds" % self.timeout)

        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        if not stdout:
            hint = stderr or "no output"
            if self.session_id and "resume" in hint.lower():
                self.session_id = None
                hint += " (dropping the session id; the next turn starts fresh)"
            return AgentReply(text="", error="claude produced no output: %s" % hint[:800])

        try:
            data = self._extract(json.loads(stdout))
        except ValueError:
            # A plain-text reply is still a reply.
            return AgentReply(text=stdout, meta={"backend": self.backend, "raw_text": True})

        text = data.get("result") or data.get("text") or ""
        session_id = data.get("session_id")
        if session_id:
            self.session_id = session_id
        meta = {
            "backend": self.backend,
            "session_id": session_id,
            "model": data.get("model") or self.model,
            "cost_usd": data.get("total_cost_usd"),
            "duration_ms": data.get("duration_ms"),
            "num_turns": data.get("num_turns"),
        }
        if data.get("is_error") or proc.returncode != 0:
            detail = str(data.get("error") or text or stderr or "claude exited %d" % proc.returncode)
            if "not logged in" in detail.lower() or "/login" in detail.lower():
                detail = "Claude Code is not signed in. Run: %s" % LOGIN_HINT
            return AgentReply(text=str(text), meta=meta, error=detail[:800])
        return AgentReply(text=str(text), meta=meta)

    @classmethod
    def probe(cls, config: Optional[Dict[str, Any]] = None) -> Probe:
        binary = Adapter.which(*DEFAULT_CANDIDATES)
        if not binary:
            return Probe(
                ok=False,
                detail="`claude` CLI not found",
                fix="%s   then:  claude auth login" % INSTALL_HINT,
            )
        logged_in, detail = read_auth_status(binary)
        if logged_in is False:
            return Probe(ok=False, detail="installed but %s" % detail, fix=LOGIN_HINT)
        if logged_in is None:
            try:
                proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.SubprocessError) as exc:
                return Probe(ok=False, detail="found %s but could not run it: %s" % (binary, exc))
            if proc.returncode != 0:
                return Probe(ok=False, detail="%s --version exited %d" % (binary, proc.returncode),
                             fix=LOGIN_HINT)
            return Probe(
                ok=False,
                detail="installed, but the login state could not be read (%s)" % detail,
                fix=LOGIN_HINT,
            )
        return Probe(ok=True, detail="%s (%s)" % (detail, binary))

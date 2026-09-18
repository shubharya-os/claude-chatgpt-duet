"""ChatGPT, through OpenAI's Codex CLI.

This is the default way duet talks to ChatGPT, because it signs in with a
ChatGPT account — no API key, no per-token billing. It also gives this side its
own tools, so both peers can read, edit and run things directly.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from duet.adapters.base import Adapter, AgentReply, Probe

DEFAULT_CANDIDATES = (
    os.environ.get("DUET_CODEX_BIN", ""),
    "codex",
    "/opt/homebrew/bin/codex",
    "/usr/local/bin/codex",
    str(Path.home() / ".local" / "bin" / "codex"),
)

INSTALL_HINT = "npm install -g @openai/codex"
LOGIN_HINT = "codex login   (sign in with your ChatGPT account — no API key needed)"


def read_login_status(binary: str, timeout: int = 45) -> Tuple[Optional[bool], str]:
    """(logged_in, human description). None means it could not be determined."""
    try:
        proc = subprocess.run(
            [binary, "login", "status"], capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    low = out.lower()
    if "not logged in" in low or "please log in" in low or "run `codex login`" in low:
        return False, out.splitlines()[0] if out else "not logged in"
    if "logged in" in low:
        return True, out.splitlines()[0]
    if proc.returncode != 0:
        return False, out.splitlines()[0] if out else "codex login status exited %d" % proc.returncode
    return None, out.splitlines()[0] if out else "could not determine login state"


class CodexCliAdapter(Adapter):
    backend = "codex-cli"
    display = "ChatGPT (Codex)"
    edits_workspace = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bin = self.which(*DEFAULT_CANDIDATES) or "codex"
        self.timeout = int(self.config.get("timeout", 1800))
        self.sandbox = str(self.config.get("sandbox", "workspace-write"))
        self.extra_args: List[str] = list(self.config.get("extra_args") or [])
        # `codex exec resume --last` looked like free continuity and was not: with
        # a prompt argument and a non-tty stdin, codex decides the real prompt is
        # coming from stdin, prints "Reading additional input from stdin...", and
        # returns no final message. It cost a turn in a live session. Every prompt
        # duet builds is self-contained — task, diff, open issues, the peer's own
        # words, gate output — so a fresh session loses context the agent did not
        # need, and buys back a failure mode. Opt back in if you want it.
        self.use_resume = bool(self.config.get("use_resume", False))
        self.turns = 0

    def read_only(self) -> str:
        """Codex has a sandbox for exactly this; use it instead of asking."""
        self.sandbox = "read-only"
        return "sandbox read-only"

    def _base(self, last_message_file: str) -> List[str]:
        # --color never keeps ANSI escapes out of anything we parse; -o gives us
        # the agent's final message verbatim instead of scraped from the log.
        cmd = [
            self.bin, "exec",
            "--skip-git-repo-check",
            "--color", "never",
            "-C", self.cwd,
            "-o", last_message_file,
        ]
        if self.sandbox:
            cmd += ["--sandbox", self.sandbox]
        if self.model:
            cmd += ["--model", self.model]
        return cmd + self.extra_args

    def send(self, prompt: str, system: str = "", round_no: int = 0) -> AgentReply:
        full = ("%s\n\n---\n\n%s" % (system, prompt)) if system else prompt
        handle, last_path = tempfile.mkstemp(prefix="duet-codex-", suffix=".txt")
        os.close(handle)
        try:
            attempts: List[List[str]] = []
            if self.turns and self.use_resume:
                attempts.append(self._base(last_path) + ["resume", "--last", full])
            attempts.append(self._base(last_path) + [full])

            last_error = ""
            blip_retried = False
            index = 0
            while index < len(attempts):
                args = attempts[index]
                index += 1
                try:
                    proc = subprocess.run(
                        args,
                        cwd=self.cwd,
                        capture_output=True,
                        text=True,
                        timeout=self.timeout,
                        # `codex exec` appends piped stdin to the prompt, so an
                        # inherited pipe makes it block forever waiting for EOF.
                        # duet is almost never run from a tty, so close it.
                        stdin=subprocess.DEVNULL,
                    )
                except FileNotFoundError:
                    return AgentReply(
                        text="",
                        error="the `codex` CLI was not found. Install it with `%s`, then "
                        "`codex login` to sign in with your ChatGPT account." % INSTALL_HINT,
                    )
                except subprocess.TimeoutExpired:
                    return AgentReply(text="", error="codex timed out after %ds" % self.timeout)

                stdout = (proc.stdout or "").strip()
                combined = (stdout + "\n" + (proc.stderr or "")).strip()

                if "not logged in" in combined.lower():
                    # Confirm it before believing it. A blip that happens to say
                    # this once cost a whole round in a real session, and the
                    # login was fine before and after.
                    logged_in, detail = read_login_status(self.bin)
                    if logged_in is False:
                        return AgentReply(
                            text="", error="codex is not signed in. Run: %s" % LOGIN_HINT
                        )
                    last_error = ("codex reported a sign-in problem, but `codex login "
                                  "status` says %s. Treating it as transient: %s"
                                  % (detail, combined[:300]))
                    if not blip_retried:
                        blip_retried = True
                        attempts.append(args)      # one more go at the same call
                    continue

                final = ""
                try:
                    final = Path(last_path).read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    final = ""
                text = final or stdout

                if proc.returncode == 0 and text:
                    self.turns += 1
                    return AgentReply(
                        text=text,
                        meta={"backend": self.backend, "model": self.model,
                              "used_last_message_file": bool(final)},
                    )
                last_error = combined[:800] or "codex exited %d" % proc.returncode
            return AgentReply(text="", error=last_error)
        finally:
            try:
                os.unlink(last_path)
            except OSError:
                pass

    @classmethod
    def probe(cls, config: Optional[Dict[str, Any]] = None) -> Probe:
        binary = Adapter.which(*DEFAULT_CANDIDATES)
        if not binary:
            return Probe(
                ok=False,
                detail="`codex` CLI not found",
                fix="%s   then:  codex login" % INSTALL_HINT,
            )
        logged_in, detail = read_login_status(binary)
        if logged_in is False:
            return Probe(ok=False, detail="installed but not signed in (%s)" % detail, fix=LOGIN_HINT)
        if logged_in is None:
            return Probe(
                ok=False,
                detail="could not read the login state: %s" % detail,
                fix=LOGIN_HINT,
            )
        return Probe(ok=True, detail="%s (%s)" % (detail, binary))

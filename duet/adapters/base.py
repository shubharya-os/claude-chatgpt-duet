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


# A CLI that cannot start says nothing about whether you are signed in. These
# are what the shell and the loader say when the binary is there but unusable —
# usually a broken or missing `node` shadowing the working one on PATH.
BROKEN_RUNTIME_MARKERS = (
    "bad cpu type",
    "exec format error",
    "cannot execute",
    "command not found",
    "no such file or directory",
    "env: node",
    "dyld",
    "symbol not found",
)


def looks_unrunnable(output: str) -> str:
    """The reason this CLI could not start, or "" if it did start.

    Reporting "not signed in" for a CLI that never ran sends people to a login
    command that will fail the same way, with nothing to tell them why.
    """
    low = (output or "").lower()
    for marker in BROKEN_RUNTIME_MARKERS:
        if marker in low:
            first = next((line.strip() for line in (output or "").splitlines() if line.strip()), "")
            return first or marker
    return ""


def env_with_sibling_path(binary: str, env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """A copy of the environment with the CLI's own directory first on PATH.

    When `claude` lives in /opt/homebrew/bin, the `node` it was installed with
    is almost certainly there too — and the reason it failed to start is some
    other, broken `node` earlier on PATH. Putting the CLI's own directory first
    uses the toolchain it shipped beside, which is what a working install would
    have done anyway.
    """
    env = dict(env if env is not None else os.environ)
    directory = os.path.dirname(os.path.abspath(binary))
    if directory:
        current = env.get("PATH", "")
        if current.split(os.pathsep)[:1] != [directory]:
            env["PATH"] = directory + (os.pathsep + current if current else "")
    return env


RUNTIME_FIX = (
    "that CLI could not start at all, so its sign-in state is unknown. It is "
    "usually a broken `node` earlier on PATH than the working one — check "
    "`node -v`, and if it fails, put a working node first (e.g. "
    "/opt/homebrew/bin before /usr/local/bin)."
)


@dataclass
class Probe:
    """What `duet doctor` reports for one backend.

    `signed_in` is separate from `ok` because they are separate questions. A
    backend can be signed in and still not ready — an exhausted allowance is
    the case that prompted this — and `duet login` must not answer a quota
    problem by opening a browser and then reporting "still not signed in".
    None means the probe could not tell.
    """

    ok: bool
    detail: str
    fix: str = ""
    signed_in: Optional[bool] = None


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

    def read_only(self) -> str:
        """Stop this agent writing to the workspace, if the backend can.

        `duet review` asks for an opinion, not an edit, and asking politely in
        the prompt is not a control. Returns a short description of what was
        actually enforced, or "" when the backend cannot enforce anything — the
        caller checks the workspace afterwards either way.
        """
        return "" if self.edits_workspace else "no tools in this workspace"

    @classmethod
    def probe(cls, config: Optional[Dict[str, Any]] = None) -> Probe:
        return Probe(ok=False, detail="no probe implemented for %s" % cls.backend)

    def describe(self) -> str:
        return "%s (%s%s)" % (self.display, self.backend, ", " + self.model if self.model else "")

    def state(self) -> Dict[str, Any]:
        return {"session_id": self.session_id}

    def restore(self, state: Dict[str, Any]) -> None:
        self.session_id = (state or {}).get("session_id")

    # -- launching --------------------------------------------------------
    npm_package = ""          # set by adapters that ship as an npm CLI

    @property
    def bin(self) -> str:
        """The command this adapter runs.

        Assigning to it is how a caller says "run exactly this instead", which
        is what the tests do with a stub, so it has to rewrite the argv prefix
        too. Keeping the two as independent attributes meant a test could set
        one and silently execute the other — and the real CLI.
        """
        return getattr(self, "_bin", "")

    @bin.setter
    def bin(self, value: str) -> None:
        self._bin = value
        self.launch = [value]

    @classmethod
    def resolve_launch(cls, candidates) -> "tuple":
        """(argv prefix, binary, installed_globally).

        Prefer a real binary on PATH. Falling back to `npx -y <package>` means
        duet works with nothing installed globally — the sign-in lives in the
        agent's own config directory, so an npx-run CLI sees the same account —
        which removes the one step of setup that changes the machine outside
        duet's own directory.
        """
        found = cls.which(*candidates)
        if found:
            return [found], found, True
        npx = cls.which("npx")
        if npx and cls.npm_package:
            return [npx, "-y", cls.npm_package], npx, False
        # Nothing resolved. The bare name is still worth returning so an error
        # message can say what was looked for, but the binary is reported empty
        # so a probe can tell "found" from "gave up and guessed".
        fallback = next((c for c in candidates if c and os.sep not in c), "")
        return [fallback], "", True

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

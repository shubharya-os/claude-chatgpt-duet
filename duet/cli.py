from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from duet import __version__, ui
from duet.adapters import REGISTRY
from duet.adapters.base import Adapter
from duet.config import (
    BACKEND_ALIASES,
    DEFAULT_PAIR,
    AgentSpec,
    Config,
    config_dir,
    config_path,
    default_agents,
    load_config,
    load_env_file,
    parse_pair,
    save_config,
)
from duet.orchestrator import Orchestrator, STATUS_CONSENSUS

PREVIEW_CHARS = 700


# --------------------------------------------------------------------------
# console reporting
# --------------------------------------------------------------------------
def make_reporter(agents: List[str], verbose: bool = True, as_json: bool = False):
    def say(*parts: str) -> None:
        # A session is long and mostly waiting. Without an explicit flush, a piped
        # or redirected run shows nothing until it ends and looks hung.
        print(*parts, flush=True)

    def report(event: Dict[str, Any]) -> None:
        if as_json:
            print(json.dumps(event, default=str), flush=True)
            return
        kind = event.get("kind")
        if kind == "session_start":
            say(ui.rule())
            say(ui.bold("duet") + "  " + ui.dim(event.get("session", "")))
            for name, desc in (event.get("agents") or {}).items():
                say("  %s  %s" % (ui.agent_tag(name, agents), ui.dim(desc)))
            say("  %s %s" % (ui.dim("workspace:"), event.get("root")))
            if event.get("gate"):
                say("  %s %s" % (ui.dim("gate:     "), event["gate"]))
            say("  %s %s goes first, up to %d rounds"
                  % (ui.dim("order:    "), (event.get("order") or ["?"])[0], event.get("max_rounds", 0)))
            say(ui.rule())
        elif kind == "turn_start":
            say("\n%s %s %s" % (
                ui.dim("round %d" % event["round"]),
                ui.agent_tag(event["agent"], agents),
                ui.dim("(%s, %s) thinking..." % (event.get("role", ""), event.get("backend", ""))),
            ))
        elif kind == "turn_done":
            say("  %s %s  %s" % (
                ui.agent_tag(event["agent"], agents),
                ui.verdict_tag(event.get("verdict", "")),
                ui.dim("state %s%s" % (str(event.get("digest", ""))[:8],
                                       "" if event.get("gate_ok", True) else ", gate FAILING")),
            ))
            if verbose and event.get("message"):
                msg = event["message"].strip()
                if len(msg) > PREVIEW_CHARS:
                    msg = msg[:PREVIEW_CHARS].rstrip() + " …"
                say(ui.wrap(msg))
            if event.get("ingest") and event["ingest"] != "no change to open issues":
                say("     " + ui.dim(event["ingest"]))
            for note in event.get("notes") or []:
                say("     " + ui.yellow("note: " + note))
        elif kind == "turn_error":
            say("  " + ui.red("error: " + str(event.get("error"))[:400]))
        elif kind == "patches":
            for line in event.get("log") or []:
                say("     " + ui.dim("fs: " + line))
        elif kind == "gate_start":
            say("     " + ui.dim("running gate: %s" % event.get("command")))
        elif kind == "gate_done":
            say("     " + (ui.green("gate passed") if event.get("ok") else ui.red("gate failed (exit %s)" % event.get("exit_code"))))
        elif kind == "decision":
            say("  " + ui.yellow("→ %s: %s" % (event.get("decision"), event.get("reason"))))
        elif kind == "arbitration_start":
            say("\n" + ui.bold(ui.yellow("ARBITRATION")) + " on %s — %s" % (event.get("issue"), event.get("title")))
        elif kind == "arbitration_done":
            say("  " + ui.yellow("ruling by %s: %s" % (event.get("decider"), str(event.get("ruling"))[:200])))
        elif kind == "commit":
            say("  " + (ui.green("committed") if event.get("ok") else ui.dim("commit skipped: " + str(event.get("output"))[:120])))
        elif kind == "session_end":
            say("\n" + ui.rule())
            status = event.get("status")
            if status == STATUS_CONSENSUS:
                say(ui.green(ui.bold("BOTH AGENTS SIGNED OFF")) + "  " + ui.dim(str(event.get("reason"))))
            else:
                say(ui.yellow(ui.bold(str(status).upper())) + "  " + str(event.get("reason")))
            say("%s %d   %s %s" % (ui.dim("rounds:"), event.get("rounds", 0),
                                     ui.dim("state:"), str(event.get("digest", ""))[:8]))
            if event.get("report"):
                say(ui.dim("report: " + event["report"]))
            say(ui.rule())

    return report


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def read_task(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).expanduser().read_text(encoding="utf-8")
    task = " ".join(args.task or []).strip()
    if task == "-" or (not task and not sys.stdin.isatty()):
        return sys.stdin.read()
    return task


def build_config(args: argparse.Namespace) -> Config:
    root = str(Path(args.root).expanduser().resolve())
    load_env_file(root)
    cfg = load_config(root)
    cfg.root = root

    if not cfg.agents:
        cfg.agents = default_agents()
    if getattr(args, "pair", None):
        try:
            cfg.agents = parse_pair(args.pair)
        except ValueError as exc:
            raise SystemExit(str(exc))
    for name in ("start", "decider"):
        chosen = getattr(args, name, "")
        if chosen and chosen not in cfg.agent_names:
            raise SystemExit(
                "--%s %r is not one of this pair: %s"
                % (name, chosen, ", ".join(cfg.agent_names))
            )

    if args.gate is not None:
        cfg.gate = args.gate
    if args.rounds is not None:
        cfg.max_rounds = args.rounds
    if args.max_debate is not None:
        cfg.max_debate = args.max_debate
    if args.start:
        cfg.start = args.start
    if args.decider:
        cfg.decider = args.decider
    if args.swap is not None:
        cfg.swap_every = args.swap
    if getattr(args, "commit", False):
        cfg.commit = True
    if getattr(args, "accept", None):
        cfg.acceptance = args.accept
    if getattr(args, "accept_file", None):
        cfg.acceptance = Path(args.accept_file).expanduser().read_text(encoding="utf-8")
    return cfg


def cmd_run(args: argparse.Namespace) -> int:
    task = read_task(args)
    if not task.strip():
        print(ui.red("no task given."))
        print("usage: duet run \"build a CLI that ...\"  [--gate \"pytest -q\"]")
        return 2

    cfg = build_config(args)
    cfg.task = task

    problems = preflight(cfg)
    if problems:
        for line in problems:
            print(ui.red("✗ ") + line)
        print("\nfix both sides in one step with " + ui.bold("duet login")
              + ui.dim("  (or `duet doctor` for the full check)"))
        return 3

    orch = Orchestrator(cfg, reporter=make_reporter(cfg.agent_names, verbose=not args.quiet, as_json=args.json))
    result = orch.run()
    return 0 if result.status == STATUS_CONSENSUS else 1


def preflight(cfg: Config) -> List[str]:
    problems: List[str] = []
    for spec in cfg.agents:
        cls = REGISTRY.get(spec.backend)
        if cls is None:
            problems.append("agent %s: unknown backend %r" % (spec.name, spec.backend))
            continue
        probe = cls.probe(spec.options)
        if not probe.ok:
            line = "agent %s (%s): %s" % (spec.name, spec.backend, probe.detail)
            if probe.fix:
                line += "\n    fix: " + probe.fix
            problems.append(line)
    return problems


def cmd_doctor(args: argparse.Namespace) -> int:
    root = str(Path(args.root).expanduser().resolve())
    loaded = load_env_file(root)
    cfg = load_config(root)
    print(ui.bold("duet %s" % __version__))
    print(ui.dim("workspace: %s" % root))
    if loaded:
        print(ui.dim("loaded from .duet/.env: %s" % ", ".join(loaded)))
    if config_path(root).is_file():
        print(ui.dim("config: %s" % config_path(root)))
    else:
        print(ui.dim("config: none (using defaults) — run `duet init` to pin one"))
    print()

    ok = True
    for spec in cfg.agents:
        cls = REGISTRY.get(spec.backend)
        print(ui.bold("%s" % spec.name) + ui.dim("  (%s)" % spec.backend))
        if cls is None:
            print("  " + ui.red("✗ unknown backend"))
            ok = False
            continue
        probe = cls.probe(spec.options)
        if probe.ok:
            print("  " + ui.green("✓ ") + probe.detail)
        else:
            ok = False
            print("  " + ui.red("✗ ") + probe.detail)
            if probe.fix:
                print("    " + ui.yellow("fix: ") + probe.fix)
        print()

    # optional extras
    from duet.adapters.codex_cli import CodexCliAdapter
    from duet.workspace import Workspace

    if not any(s.backend == "codex-cli" for s in cfg.agents):
        probe = CodexCliAdapter.probe()
        print(ui.dim("optional: codex CLI — ") + (ui.green(probe.detail) if probe.ok else ui.dim(probe.detail)))
        if probe.ok:
            print(ui.dim("  (run `duet init --pair claude+codex` to give ChatGPT its own tools)"))

    ws = Workspace(root)
    print(ui.dim("git repo: ") + ("yes" if ws.is_git_repo else ui.yellow("no — duet works anyway, but a repo makes review much better")))
    if cfg.gate:
        print(ui.dim("gate: ") + cfg.gate)
    else:
        print(ui.yellow("no acceptance gate configured") + ui.dim(" — set one with --gate \"pytest -q\"; it is what keeps \"done\" honest"))

    print()
    if ok:
        print(ui.green(ui.bold("ready.")) + " try: " + ui.bold('duet run "your task here"'))
    else:
        print(ui.red(ui.bold("not ready yet")) + " — run " + ui.bold("duet login")
              + " to sign both sides in, then " + ui.bold("duet doctor") + " again")
    return 0 if ok else 1


def cmd_init(args: argparse.Namespace) -> int:
    root = str(Path(args.root).expanduser().resolve())
    load_env_file(root)
    cfg = load_config(root)
    cfg.root = root
    if getattr(args, "pair", None):
        try:
            cfg.agents = parse_pair(args.pair)
        except ValueError as exc:
            raise SystemExit(str(exc))
    elif getattr(args, "gpt_backend", None):   # older flag, still honoured
        for spec in cfg.agents:
            if spec.backend in ("codex-cli", "openai-api"):
                spec.backend = args.gpt_backend
    if args.gate is not None:
        cfg.gate = args.gate
    if args.rounds is not None:
        cfg.max_rounds = args.rounds

    path = save_config(cfg, root)
    print(ui.green("wrote ") + str(path))

    env_path = config_dir(root) / ".env"
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    uses_api = any(s.backend == "openai-api" for s in cfg.agents)
    if uses_api and not has_key:
        print()
        print(ui.yellow("ChatGPT needs an API key."))
        print("  Get one at " + ui.bold("https://platform.openai.com/api-keys") + ", then either:")
        print("    export OPENAI_API_KEY=sk-...")
        print("  or put it in " + ui.bold(str(env_path)) + " as:")
        print("    OPENAI_API_KEY=sk-...")
        print(ui.dim("  (.duet/.env is gitignored by this repo's template; never commit a key)"))

    gitignore = Path(root) / ".gitignore"
    entries = [".duet/sessions/", ".duet/.env", ".duet/cache.json"]
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
        missing = [e for e in entries if e not in existing]
        if missing:
            with gitignore.open("a", encoding="utf-8") as fh:
                fh.write(("\n" if existing and not existing.endswith("\n") else "") + "\n".join(missing) + "\n")
            print(ui.dim("added %s to .gitignore" % ", ".join(missing)))
    except OSError:
        pass

    print()
    return cmd_doctor(args)


def cmd_login(args: argparse.Namespace) -> int:
    """Walk both sign-ins. Neither one involves an API key.

    duet never handles your credentials: each CLI runs its own browser sign-in
    attached to your terminal, and duet only asks afterwards whether it worked.
    """
    from duet.adapters.claude_code import ClaudeCodeAdapter
    from duet.adapters.codex_cli import CodexCliAdapter

    root = str(Path(args.root).expanduser().resolve())
    load_env_file(root)
    cfg = load_config(root)

    plan = []
    for spec in cfg.agents:
        if spec.backend == "claude-code":
            plan.append((spec.name, ClaudeCodeAdapter, ["auth", "login"], "Claude"))
        elif spec.backend == "codex-cli":
            plan.append((spec.name, CodexCliAdapter, ["login"], "ChatGPT"))
        elif spec.backend == "openai-api":
            print(ui.yellow("%s uses the API-key backend (openai-api); nothing to sign in to." % spec.name))
            print(ui.dim("  switch it to a ChatGPT login with: duet init --pair claude+codex"))

    if args.agent:
        plan = [item for item in plan if item[0] == args.agent]
        if not plan:
            print(ui.red("no agent named %r uses a sign-in backend" % args.agent))
            return 2

    failures = 0
    for name, cls, sub, account in plan:
        probe = cls.probe()
        if probe.ok and not args.force:
            print(ui.green("✓ ") + "%s is already signed in — %s" % (name, probe.detail))
            continue
        binary = cls(name=name, cwd=root).bin
        if not Adapter.which(binary):
            print(ui.red("✗ ") + "%s: %s" % (name, probe.detail))
            if probe.fix:
                print("    " + ui.yellow("install it first: ") + probe.fix)
            failures += 1
            continue
        print()
        print(ui.bold("signing %s in with your %s account" % (name, account)))
        print(ui.dim("  running: %s %s" % (binary, " ".join(sub))))
        print(ui.dim("  this opens your browser; duet never sees your credentials."))
        try:
            code = subprocess.call([binary, *sub])
        except (OSError, KeyboardInterrupt) as exc:
            print(ui.red("  could not run it: %s" % exc))
            failures += 1
            continue
        after = cls.probe()
        if after.ok:
            print(ui.green("✓ ") + "%s signed in — %s" % (name, after.detail))
        else:
            failures += 1
            print(ui.red("✗ ") + "%s is still not signed in (%s exited %d)" % (name, sub[0], code))
            if after.fix:
                print("    " + ui.yellow("try: ") + after.fix)

    print()
    return cmd_doctor(args) if not failures else 1


def skill_source() -> Path:
    return Path(__file__).resolve().parent / "skill" / "SKILL.md"


def cmd_skill(args: argparse.Namespace) -> int:
    """Install the Claude Code skill, so `duet` is reachable from inside Claude."""
    source = skill_source()
    if not source.is_file():
        print(ui.red("the packaged skill is missing at %s" % source))
        return 1

    target_dir = Path(args.dir).expanduser() if args.dir else Path.home() / ".claude" / "skills" / "duet"
    target = target_dir / "SKILL.md"

    if args.action == "path":
        print(source)
        return 0
    if args.action == "show":
        print(source.read_text(encoding="utf-8"))
        return 0

    if target.is_file() and not args.force:
        existing = target.read_text(encoding="utf-8", errors="replace")
        if existing == source.read_text(encoding="utf-8"):
            print(ui.green("✓ ") + "already installed and up to date: %s" % target)
            return 0
        print(ui.yellow("a different version is already installed at %s" % target))
        print(ui.dim("  re-install it with: ") + ui.bold("duet skill install --force"))
        return 1

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError as exc:
        print(ui.red("could not write %s: %s" % (target, exc)))
        return 1

    print(ui.green("✓ ") + "installed the duet skill to %s" % target)
    print()
    print("Claude Code will pick it up in a new session. Then ask it things like:")
    print(ui.dim("  ") + ui.bold('"get a second opinion on this from ChatGPT"'))
    print(ui.dim("  ") + ui.bold('"have ChatGPT review the diff before I push"'))
    print(ui.dim("  ") + ui.bold('"work on this with ChatGPT until you both agree"'))
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Run the whole loop with scripted agents: no keys, no network."""
    from duet.adapters.mock import MockAdapter, envelope

    root = args.root if args.root != "." else tempfile.mkdtemp(prefix="duet-demo-")
    root = str(Path(root).expanduser().resolve())
    Path(root).mkdir(parents=True, exist_ok=True)

    task = "Write greet.py: a function greet(name) returning 'Hello, <name>!' that rejects an empty name."
    gate = "%s greet.py" % sys.executable

    good = (
        "def greet(name):\n"
        "    if not name or not name.strip():\n"
        "        raise ValueError('name must not be empty')\n"
        "    return 'Hello, %s!' % name.strip()\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    assert greet('Ada') == 'Hello, Ada!'\n"
        "    try:\n"
        "        greet('  ')\n"
        "    except ValueError:\n"
        "        pass\n"
        "    else:\n"
        "        raise AssertionError('empty name must raise')\n"
        "    print('ok')\n"
    )
    weak = "def greet(name):\n    return 'Hello, ' + name + '!'\n"

    claude = MockAdapter(name="claude", cwd=root, config={"script": [
        envelope("First pass at greet.py.", "CONTINUE",
                 patches=[{"path": "greet.py", "action": "write", "content": weak}],
                 summary="initial greet()"),
        envelope("Fair catch — empty names now raise, and I added a self-check so the gate proves it.",
                 "CONTINUE", resolves=["no-empty-name-validation"],
                 patches=[{"path": "greet.py", "action": "write", "content": good}],
                 summary="validate empty names"),
        envelope("Checked it against the task: both requirements are covered and the gate is green. Shipping.",
                 "DONE", confidence=0.9),
    ]})
    gpt = MockAdapter(name="gpt", cwd=root, config={"script": [
        envelope("This ignores half the task: an empty name returns 'Hello, !' instead of being rejected.",
                 "CONTINUE", issues=[{"id": "no-empty-name-validation",
                                      "title": "empty name is accepted",
                                      "severity": "blocker",
                                      "detail": "The task says an empty name must be rejected. Raise ValueError on empty or whitespace-only input."}]),
        envelope("Verified: whitespace-only input raises and the gate runs the check. I agree this is done.",
                 "DONE", confidence=0.9),
    ]})

    cfg = Config(task=task, root=root, gate=gate, max_rounds=8,
                 agents=[AgentSpec("claude", "mock"), AgentSpec("gpt", "mock")], start="claude")
    orch = Orchestrator(cfg, reporter=make_reporter(["claude", "gpt"], verbose=not args.quiet, as_json=args.json),
                        adapters={"claude": claude, "gpt": gpt})
    result = orch.run()
    if not args.json:
        print()
        print(ui.dim("that was the real orchestrator with scripted peers — "
                     "same envelopes, same gate, same double sign-off."))
        print(ui.dim("demo workspace: %s" % root))
        print(ui.dim("see the whole argument:  ") + ui.bold("duet report --transcript -C %s" % root))
    return 0 if result.status == STATUS_CONSENSUS else 1


def _sessions(root: str) -> List[Path]:
    base = Path(root).resolve() / ".duet" / "sessions"
    if not base.is_dir():
        return []
    return sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.name)


def cmd_sessions(args: argparse.Namespace) -> int:
    sessions = _sessions(args.root)
    if not sessions:
        print("no sessions yet in %s" % (Path(args.root).resolve() / ".duet" / "sessions"))
        return 0
    for path in sessions:
        status = "?"
        try:
            data = json.loads((path / "state.json").read_text(encoding="utf-8"))
            result = data.get("result") or {}
            status = result.get("status") or "incomplete"
        except (OSError, ValueError):
            pass
        mark = ui.green(status) if status == STATUS_CONSENSUS else ui.yellow(status)
        print("%s  %s" % (path.name, mark))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    sessions = _sessions(args.root)
    if args.session:
        sessions = [p for p in sessions if p.name == args.session] or sessions
    if not sessions:
        print("no sessions found")
        return 1
    target = sessions[-1] if not args.session else sessions[0]
    name = "transcript.md" if args.transcript else "report.md"
    path = target / name
    if not path.is_file():
        print("no %s for session %s" % (name, target.name))
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


# --------------------------------------------------------------------------
# verify: does the last sign-off still describe this workspace?
# --------------------------------------------------------------------------
VERIFY_OK = 0        # signed state still present, gate still green
VERIFY_STALE = 1     # a sign-off exists but no longer holds
VERIFY_NOTHING = 2   # nothing to verify against (no session, or none recorded)


def _session_state(path: Path) -> Optional[Dict[str, Any]]:
    """The saved state of one session, or None if it never wrote one."""
    try:
        return json.loads((path / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _last_recorded_session(root: str) -> Tuple[Optional[Path], Optional[Dict[str, Any]], List[str]]:
    """The newest session that actually saved state, plus the ones skipped.

    A session killed before its first save leaves a directory with nothing but
    events in it. There is no sign-off in such a directory to check, so verify
    walks back to the newest one that has a state file and says which it used.
    """
    skipped: List[str] = []
    for path in reversed(_sessions(root)):
        data = _session_state(path)
        if data is not None:
            return path, data, skipped
        skipped.append(path.name)
    return None, None, skipped


def _signed_state(data: Dict[str, Any]) -> Tuple[str, Dict[str, Dict[str, Any]], str]:
    """The state id both agents signed, the per-agent sign-offs, and why not.

    Returns ("", signoffs, reason) unless every agent signed off on one and the
    same digest — a single DONE is not a sign-off, and two DONEs against
    different states are not either.
    """
    state = data.get("state") or {}
    signoffs = {k: v for k, v in (state.get("signoffs") or {}).items() if isinstance(v, dict)}
    agents = list(state.get("agents") or [])
    if not agents:
        agents = [a.get("name") for a in (data.get("config") or {}).get("agents") or []]
    missing = [a for a in agents if a not in signoffs]
    if not signoffs and not agents:
        return "", signoffs, "the session recorded no sign-off"
    if not signoffs or missing:
        return "", signoffs, "%s never signed off" % ", ".join(missing or agents)
    digests = {str(s.get("digest") or "") for s in signoffs.values()}
    if len(digests) > 1:
        detail = ", ".join("%s %s" % (a, str(s.get("digest") or "?")[:8]) for a, s in sorted(signoffs.items()))
        return "", signoffs, "the agents signed off on different states (%s)" % detail
    only = digests.pop()
    if not only:
        return "", signoffs, "the recorded sign-off has no state id"
    return only, signoffs, ""


def cmd_verify(args: argparse.Namespace) -> int:
    from duet.workspace import Workspace

    root = str(Path(args.root).expanduser().resolve())
    as_json = getattr(args, "json", False)
    out: Dict[str, Any] = {"kind": "verify", "root": root}

    def finish(code: int, reason: str) -> int:
        if as_json:
            out["reason"] = reason
            out["ok"] = code == VERIFY_OK
            out["exit_code"] = code
            print(json.dumps(out, default=str), flush=True)
        return code

    sessions = _sessions(root)
    skipped: List[str] = []
    if getattr(args, "session", None):
        match = [p for p in sessions if p.name == args.session]
        out["session"] = args.session
        if not match:
            if not as_json:
                print(ui.red("no session %r in %s" % (args.session, Path(root) / ".duet" / "sessions")))
            return finish(VERIFY_NOTHING, "no such session")
        path = match[0]
        data = _session_state(path)
        if data is None:
            if not as_json:
                print(ui.yellow("session %s saved no state.json — there is no sign-off in it to check" % path.name))
            return finish(VERIFY_NOTHING, "session recorded no state")
    else:
        path, data, skipped = _last_recorded_session(root)

    if data is None or path is None:
        where = Path(root) / ".duet" / "sessions"
        if not as_json:
            if skipped:
                print(ui.yellow("no session here recorded a sign-off") +
                      ui.dim(" (%d session dir(s) saved no state.json)" % len(skipped)))
            else:
                print("no sessions yet in %s" % where)
            print(ui.dim("nothing has been signed off in this workspace — run ")
                  + ui.bold('duet run "..."') + ui.dim(" first."))
        out["session"] = None
        return finish(VERIFY_NOTHING, "no session has recorded a sign-off here")

    signed, signoffs, why_not = _signed_state(data)
    cfg_data = data.get("config") or {}
    result = data.get("result") or {}
    gate_cmd = args.gate if getattr(args, "gate", None) is not None else (cfg_data.get("gate") or "")
    try:
        timeout = int(cfg_data.get("gate_timeout") or 900)
    except (TypeError, ValueError):
        timeout = 900

    ws = Workspace(root, gate=gate_cmd or None, gate_timeout=timeout)
    # Digest first, gate second: a gate is free to write files (coverage data,
    # build output), and hashing after it ran would compare the signed state
    # against a workspace the gate itself had already changed.
    now = ws.digest()
    matches = bool(signed) and signed == now
    gate = ws.run_gate()

    out.update(
        session=path.name,
        outcome=result.get("status") or "incomplete",
        signed_digest=signed or None,
        signoffs={a: {"round": s.get("round"), "digest": s.get("digest")} for a, s in sorted(signoffs.items())},
        current_digest=now,
        match=matches,
        skipped_sessions=skipped,
        gate={
            "command": gate.command,
            "skipped": gate.skipped,
            "ok": gate.ok,
            "exit_code": gate.exit_code,
        },
    )

    if not as_json:
        print(ui.bold("duet verify") + "  " + ui.dim(root))
        if skipped:
            print(ui.dim("  (skipped %s — saved no state.json)" % ", ".join(skipped)))
        print("  %s %s  %s" % (ui.dim("session:  "), path.name,
                               ui.dim("outcome: %s" % (result.get("status") or "incomplete"))))
        if signed:
            who = ", ".join("%s r%s" % (a, s.get("round", "?")) for a, s in sorted(signoffs.items()))
            print("  %s %s  %s" % (ui.dim("signed:   "), signed, ui.dim("(%s)" % who)))
        else:
            print("  %s %s" % (ui.dim("signed:   "), ui.yellow("none — " + why_not)))
        print("  %s %s" % (ui.dim("now:      "), now))
        print("  %s %s" % (ui.dim("match:    "),
                           ui.green("yes") if matches else ui.red("no") if signed else ui.yellow("n/a")))
        if gate.skipped:
            print("  %s %s" % (ui.dim("gate:     "), ui.yellow("none recorded for this session")
                              + ui.dim(" — re-run with --gate \"...\" to check one")))
        else:
            print("  %s %s  %s" % (ui.dim("gate:     "), gate.command,
                                   ui.green("passed") if gate.ok else ui.red("FAILED (exit %d)" % gate.exit_code)))
        print()

    if not signed:
        if not as_json:
            print(ui.red("that session never produced a double sign-off") + " — " + why_not + ".")
            print(ui.dim("nothing here was agreed, so there is nothing to still hold."))
        return finish(VERIFY_STALE, why_not)

    problems: List[str] = []
    if not matches:
        problems.append("the workspace has changed since sign-off")
    if not gate.ok:
        problems.append("the acceptance gate no longer passes")

    if not problems:
        if not as_json:
            print(ui.green(ui.bold("the sign-off still holds.")) + " " +
                  ("gate re-run and green." if not gate.skipped else "no gate was recorded, so none was re-run."))
        return finish(VERIFY_OK, "sign-off still holds")

    if not as_json:
        print(ui.red(ui.bold("the sign-off no longer holds.")))
        for line in problems:
            print("  " + ui.red("✗ ") + line)
        if not matches:
            print(ui.dim("  the state both agents approved was %s; this workspace is %s." % (signed, now)))
            print(ui.dim("  see what changed with: ") + ui.bold("git diff") +
                  ui.dim("   then re-run: ") + ui.bold('duet run "..."'))
        if not gate.ok and not gate.skipped:
            print()
            print(gate.render(2000))
    return finish(VERIFY_STALE, "; ".join(problems))


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="duet",
        description="Claude Code and ChatGPT on the same task, debating until both sign off.",
    )
    parser.add_argument("--version", action="version", version="duet %s" % __version__)
    sub = parser.add_subparsers(dest="command")

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("-C", "--root", default=".", help="workspace directory (default: .)")
        p.add_argument("-q", "--quiet", action="store_true", help="verdicts only, no message previews")
        p.add_argument("--json", action="store_true", help="emit machine-readable events on stdout")

    p_run = sub.add_parser("run", help="run a session")
    p_run.add_argument("task", nargs="*", help="what the two agents should build or solve")
    common(p_run)
    p_run.add_argument("-f", "--file", help="read the task from a file")
    p_run.add_argument("--accept", help="acceptance criteria, in prose")
    p_run.add_argument("--accept-file", help="read acceptance criteria from a file")
    p_run.add_argument("--gate", help="command that must pass before either agent may finish, e.g. \"pytest -q\"")
    p_run.add_argument("--rounds", type=int, help="maximum rounds (default 12)")
    p_run.add_argument("--max-debate", type=int, help="rounds an issue may stay open before arbitration (default 3)")
    p_run.add_argument(
        "--pair",
        metavar="A+B",
        help="which two agents, and who leads (default: %s). "
        "A and B are any of: %s. Add a model with a colon, e.g. claude:opus+codex."
        % (DEFAULT_PAIR, ", ".join(sorted(set(BACKEND_ALIASES)))),
    )
    p_run.add_argument("--start", help="who takes the first turn (defaults to the left of --pair)")
    p_run.add_argument("--decider", help="who rules on deadlocked issues")
    p_run.add_argument("--swap", type=int, help="swap lead/reviewer every N rounds (0 = never)")
    p_run.add_argument("--commit", action="store_true", help="git-commit the result when both sign off")
    p_run.set_defaults(func=cmd_run)

    p_doctor = sub.add_parser("doctor", help="check that both agents are reachable")
    common(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    p_init = sub.add_parser("init", help="write .duet/config.json and check the setup")
    common(p_init)
    p_init.add_argument("--pair", metavar="A+B", help="which two agents to pin (default: %s)" % DEFAULT_PAIR)
    p_init.add_argument("--gpt-backend", choices=["openai-api", "codex-cli"], help=argparse.SUPPRESS)
    p_init.add_argument("--gate")
    p_init.add_argument("--rounds", type=int)
    p_init.set_defaults(func=cmd_init)

    p_login = sub.add_parser("login", help="sign both agents in (no API key involved)")
    common(p_login)
    p_login.add_argument("agent", nargs="?", choices=["claude", "gpt"], help="sign in just one side")
    p_login.add_argument("--force", action="store_true", help="re-run the sign-in even if it looks connected")
    p_login.set_defaults(func=cmd_login)

    p_skill = sub.add_parser("skill", help="install the Claude Code skill for duet")
    common(p_skill)
    p_skill.add_argument("action", nargs="?", default="install",
                         choices=["install", "path", "show"],
                         help="install it (default), print its source path, or print it")
    p_skill.add_argument("--dir", help="install somewhere other than ~/.claude/skills/duet")
    p_skill.add_argument("--force", action="store_true", help="overwrite an existing copy")
    p_skill.set_defaults(func=cmd_skill)

    p_demo = sub.add_parser("demo", help="run the full loop with scripted agents (no keys, no network)")
    common(p_demo)
    p_demo.set_defaults(func=cmd_demo)

    p_sessions = sub.add_parser("sessions", help="list past sessions")
    common(p_sessions)
    p_sessions.set_defaults(func=cmd_sessions)

    p_report = sub.add_parser("report", help="print the report for a session (default: the last one)")
    common(p_report)
    p_report.add_argument("session", nargs="?", help="session id")
    p_report.add_argument("--transcript", action="store_true", help="print the full transcript instead")
    p_report.set_defaults(func=cmd_report)

    p_verify = sub.add_parser(
        "verify",
        help="re-run the gate and check the last sign-off still describes this workspace",
    )
    common(p_verify)
    p_verify.add_argument("session", nargs="?", help="session id (default: the last one that saved state)")
    p_verify.add_argument("--gate", help="check against this command instead of the one the session recorded")
    p_verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    # Sessions are long and mostly waiting on an agent. Piped or redirected runs
    # block-buffer by default, which makes a working session look hung.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):  # pragma: no cover - very old interpreters
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        print("\n" + ui.dim("first time? ") + ui.bold("duet login") + ui.dim(" → ")
              + ui.bold("duet doctor") + ui.dim(" → ") + ui.bold("duet demo"))
        return 0
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\n" + ui.yellow("interrupted"))
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

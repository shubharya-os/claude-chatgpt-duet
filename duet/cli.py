from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from duet import __version__, ui
from duet.adapters import REGISTRY
from duet.adapters.base import Adapter
from duet.config import (
    AgentSpec,
    Config,
    config_dir,
    config_path,
    default_agents,
    load_config,
    load_env_file,
    save_config,
)
from duet.orchestrator import Orchestrator, STATUS_CONSENSUS

PREVIEW_CHARS = 700


# --------------------------------------------------------------------------
# console reporting
# --------------------------------------------------------------------------
def make_reporter(agents: List[str], verbose: bool = True, as_json: bool = False):
    def report(event: Dict[str, Any]) -> None:
        if as_json:
            print(json.dumps(event, default=str), flush=True)
            return
        kind = event.get("kind")
        if kind == "session_start":
            print(ui.rule())
            print(ui.bold("duet") + "  " + ui.dim(event.get("session", "")))
            for name, desc in (event.get("agents") or {}).items():
                print("  %s  %s" % (ui.agent_tag(name, agents), ui.dim(desc)))
            print("  %s %s" % (ui.dim("workspace:"), event.get("root")))
            if event.get("gate"):
                print("  %s %s" % (ui.dim("gate:     "), event["gate"]))
            print("  %s %s goes first, up to %d rounds"
                  % (ui.dim("order:    "), (event.get("order") or ["?"])[0], event.get("max_rounds", 0)))
            print(ui.rule())
        elif kind == "turn_start":
            print("\n%s %s %s" % (
                ui.dim("round %d" % event["round"]),
                ui.agent_tag(event["agent"], agents),
                ui.dim("(%s, %s) thinking..." % (event.get("role", ""), event.get("backend", ""))),
            ))
        elif kind == "turn_done":
            print("  %s %s  %s" % (
                ui.agent_tag(event["agent"], agents),
                ui.verdict_tag(event.get("verdict", "")),
                ui.dim("state %s%s" % (str(event.get("digest", ""))[:8],
                                       "" if event.get("gate_ok", True) else ", gate FAILING")),
            ))
            if verbose and event.get("message"):
                msg = event["message"].strip()
                if len(msg) > PREVIEW_CHARS:
                    msg = msg[:PREVIEW_CHARS].rstrip() + " …"
                print(ui.wrap(msg))
            if event.get("ingest") and event["ingest"] != "no change to open issues":
                print("     " + ui.dim(event["ingest"]))
            for note in event.get("notes") or []:
                print("     " + ui.yellow("note: " + note))
        elif kind == "turn_error":
            print("  " + ui.red("error: " + str(event.get("error"))[:400]))
        elif kind == "patches":
            for line in event.get("log") or []:
                print("     " + ui.dim("fs: " + line))
        elif kind == "gate_start":
            print("     " + ui.dim("running gate: %s" % event.get("command")))
        elif kind == "gate_done":
            print("     " + (ui.green("gate passed") if event.get("ok") else ui.red("gate failed (exit %s)" % event.get("exit_code"))))
        elif kind == "decision":
            print("  " + ui.yellow("→ %s: %s" % (event.get("decision"), event.get("reason"))))
        elif kind == "arbitration_start":
            print("\n" + ui.bold(ui.yellow("ARBITRATION")) + " on %s — %s" % (event.get("issue"), event.get("title")))
        elif kind == "arbitration_done":
            print("  " + ui.yellow("ruling by %s: %s" % (event.get("decider"), str(event.get("ruling"))[:200])))
        elif kind == "commit":
            print("  " + (ui.green("committed") if event.get("ok") else ui.dim("commit skipped: " + str(event.get("output"))[:120])))
        elif kind == "session_end":
            print("\n" + ui.rule())
            status = event.get("status")
            if status == STATUS_CONSENSUS:
                print(ui.green(ui.bold("BOTH AGENTS SIGNED OFF")) + "  " + ui.dim(str(event.get("reason"))))
            else:
                print(ui.yellow(ui.bold(str(status).upper())) + "  " + str(event.get("reason")))
            print("%s %d   %s %s" % (ui.dim("rounds:"), event.get("rounds", 0),
                                     ui.dim("state:"), str(event.get("digest", ""))[:8]))
            if event.get("report"):
                print(ui.dim("report: " + event["report"]))
            print(ui.rule())

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
    by_name = {a.name: a for a in cfg.agents}
    if getattr(args, "gpt_backend", None):
        by_name["gpt"].backend = args.gpt_backend
    if getattr(args, "claude_model", None):
        by_name["claude"].model = args.claude_model
    if getattr(args, "gpt_model", None):
        by_name["gpt"].model = args.gpt_model

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
            print(ui.dim("  (run `duet init --gpt-backend codex-cli` to give ChatGPT its own tools)"))

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
    if args.gpt_backend:
        for spec in cfg.agents:
            if spec.name == "gpt":
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
            print(ui.dim("  switch it to a ChatGPT login with: duet init --gpt-backend codex-cli"))

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
        print(ui.dim("demo workspace: %s" % root))
        print(ui.dim("that was the real orchestrator with scripted peers — "
                     "same envelopes, same gate, same double sign-off."))
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
    p_run.add_argument("--start", choices=["claude", "gpt"], help="who takes the first turn")
    p_run.add_argument("--decider", choices=["claude", "gpt"], help="who rules on deadlocked issues")
    p_run.add_argument("--swap", type=int, help="swap lead/reviewer every N rounds (0 = never)")
    p_run.add_argument("--gpt-backend", choices=["openai-api", "codex-cli"], help="how to reach ChatGPT")
    p_run.add_argument("--claude-model", help="model for the Claude Code side")
    p_run.add_argument("--gpt-model", help="model for the ChatGPT side")
    p_run.add_argument("--commit", action="store_true", help="git-commit the result when both sign off")
    p_run.set_defaults(func=cmd_run)

    p_doctor = sub.add_parser("doctor", help="check that both agents are reachable")
    common(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    p_init = sub.add_parser("init", help="write .duet/config.json and check the setup")
    common(p_init)
    p_init.add_argument("--gpt-backend", choices=["openai-api", "codex-cli"])
    p_init.add_argument("--gate")
    p_init.add_argument("--rounds", type=int)
    p_init.set_defaults(func=cmd_init)

    p_login = sub.add_parser("login", help="sign both agents in (no API key involved)")
    common(p_login)
    p_login.add_argument("agent", nargs="?", choices=["claude", "gpt"], help="sign in just one side")
    p_login.add_argument("--force", action="store_true", help="re-run the sign-in even if it looks connected")
    p_login.set_defaults(func=cmd_login)

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

    return parser


def main(argv: Optional[List[str]] = None) -> int:
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

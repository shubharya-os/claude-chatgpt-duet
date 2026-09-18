"""The loop.

One agent moves, the workspace changes, the gate runs, the other agent answers.
Nothing ends until both of them sign the same state.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from duet import prompts
from duet.adapters import build as build_adapter
from duet.adapters.base import Adapter
from duet.config import Config
from duet.consensus import DebateState, Decision
from duet.protocol import Envelope, parse_envelope
from duet.workspace import GateResult, Workspace

Reporter = Callable[[Dict[str, Any]], None]

STATUS_CONSENSUS = "consensus"
STATUS_EXHAUSTED = "exhausted"
STATUS_BLOCKED = "blocked"
STATUS_ERROR = "error"
STATUS_INTERRUPTED = "interrupted"


@dataclass
class SessionResult:
    status: str
    rounds: int
    digest: str
    reason: str = ""
    gate: Optional[GateResult] = None
    session_dir: str = ""
    report: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_CONSENSUS


@dataclass
class TurnRecord:
    round: int
    agent: str
    role: str
    envelope: Envelope
    digest: str
    patch_log: List[str] = field(default_factory=list)
    gate: Optional[GateResult] = None
    ingest: str = ""
    error: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


class Orchestrator:
    def __init__(
        self,
        config: Config,
        reporter: Optional[Reporter] = None,
        session_id: Optional[str] = None,
        adapters: Optional[Dict[str, Adapter]] = None,
    ):
        self.config = config
        self.reporter = reporter or (lambda event: None)
        self.workspace = Workspace(config.root, gate=config.gate or None, gate_timeout=config.gate_timeout)
        self.state = DebateState(
            config.agent_names, max_debate=config.max_debate, stall_limit=config.stall_limit
        )
        self.session_id = session_id or time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self.session_dir = Path(config.root).resolve() / ".duet" / "sessions" / self.session_id
        self.turns: List[TurnRecord] = []
        self.history: List[str] = []
        self.pending_directive: Dict[str, str] = {}
        self.pending_reads: Dict[str, List[str]] = {}
        self.pending_patch_log: Dict[str, List[str]] = {}
        self.last_envelope: Dict[str, Envelope] = {}
        self._gate_cache: Dict[str, GateResult] = {}
        self.adapters: Dict[str, Adapter] = adapters or {}
        if not self.adapters:
            for spec in config.agents:
                self.adapters[spec.name] = build_adapter(
                    spec.backend,
                    name=spec.name,
                    cwd=config.root,
                    model=spec.model,
                    config=spec.options,
                )
        if config.gate:
            # An agent that cannot run the gate is reviewing on hearsay.
            for adapter in self.adapters.values():
                adapter.allow_gate(config.gate)

    # -- plumbing ---------------------------------------------------------
    def emit(self, kind: str, **payload: Any) -> None:
        event = dict(kind=kind, t=time.time(), **payload)
        self.reporter(event)
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            with (self.session_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, default=str) + "\n")
        except OSError:
            pass

    def role_of(self, agent: str, round_no: int) -> str:
        order = self.config.order()
        lead = order[0]
        if self.config.swap_every:
            swaps = (round_no - 1) // max(1, self.config.swap_every)
            if swaps % 2:
                lead = order[1]
        return prompts.ROLE_LEAD if agent == lead else prompts.ROLE_REVIEWER

    def decider(self) -> str:
        if self.config.decider and self.config.decider in self.config.agent_names:
            return self.config.decider
        for spec in self.config.agents:
            if self.adapters[spec.name].edits_workspace:
                return spec.name
        return self.config.agent_names[0]

    def gate_for(self, digest: str) -> GateResult:
        if digest not in self._gate_cache:
            if self.workspace.gate:
                self.emit("gate_start", command=self.workspace.gate, digest=digest)
            result = self.workspace.run_gate()
            self._gate_cache[digest] = result
            if self.workspace.gate:
                self.emit("gate_done", ok=result.ok, exit_code=result.exit_code, digest=digest)
        return self._gate_cache[digest]

    # -- one turn ---------------------------------------------------------
    def _build_prompt(self, agent: str, round_no: int, directive: str, digest: str) -> str:
        peer = self.state.peer_of(agent)
        peer_env = self.last_envelope.get(peer)
        gate = self.gate_for(digest)
        files: Dict[str, str] = {}
        for path in self.pending_reads.pop(agent, [])[:12]:
            files[path] = self.workspace.read(path)
        return prompts.turn_prompt(
            task=self.config.task,
            acceptance=self.config.acceptance,
            round_no=round_no,
            max_rounds=self.config.max_rounds,
            role=self.role_of(agent, round_no),
            peer=peer,
            peer_message=peer_env.message if peer_env else "",
            peer_verdict=peer_env.verdict if peer_env else "",
            digest=digest,
            workspace_view=self.workspace.diff(),
            gate_text=gate.render(),
            against_you=self.state.issues_against(agent),
            yours=[i for i in self.state.open_issues() if i.raised_by == agent],
            history=self.history,
            why_open=self.state.why_not_done(digest, gate.ok),
            directive=directive,
            files=files or None,
            patch_log=self.pending_patch_log.pop(agent, None),
        )

    def _system_prompt(self, agent: str, round_no: int) -> str:
        adapter = self.adapters[agent]
        peer = self.state.peer_of(agent)
        peer_adapter = self.adapters[peer]
        return prompts.system_prompt(
            name=agent,
            display=adapter.display,
            peer=peer,
            peer_display=peer_adapter.display,
            role=self.role_of(agent, round_no),
            root=self.config.root,
            edits_workspace=adapter.edits_workspace,
        )

    def take_turn(self, agent: str, round_no: int, directive: str = "", ingest: bool = True) -> TurnRecord:
        adapter = self.adapters[agent]
        digest_before = self.workspace.digest()
        prompt = self._build_prompt(agent, round_no, directive, digest_before)
        role = self.role_of(agent, round_no)

        self.emit("turn_start", round=round_no, agent=agent, role=role, backend=adapter.backend)
        reply = adapter.send(prompt, system=self._system_prompt(agent, round_no), round_no=round_no)

        if not reply.ok and not reply.text.strip():
            record = TurnRecord(
                round=round_no, agent=agent, role=role, envelope=Envelope(agent=agent, round=round_no),
                digest=digest_before, error=reply.error, meta=reply.meta,
            )
            self.turns.append(record)
            self.emit("turn_error", round=round_no, agent=agent, error=reply.error)
            return record

        env = parse_envelope(reply.text, agent=agent, round_no=round_no)
        if reply.error:
            env.notes.append("backend reported: %s" % reply.error)

        patch_log: List[str] = []
        if env.patches:
            patch_log = self.workspace.apply_patches(env.patches)
            self.emit("patches", round=round_no, agent=agent, log=patch_log)
            peer = self.state.peer_of(agent)
            self.pending_patch_log[peer] = patch_log
            self.pending_patch_log[agent] = patch_log

        reads = env.meta.get("reads") or []
        if reads:
            self.pending_reads[agent] = list(reads)

        digest_after = self.workspace.digest()
        gate = self.gate_for(digest_after)

        record = TurnRecord(
            round=round_no, agent=agent, role=role, envelope=env, digest=digest_after,
            patch_log=patch_log, gate=gate, meta=reply.meta,
        )
        if ingest:
            report = self.state.ingest(env, digest_after)
            record.ingest = report.render()
        self.last_envelope[agent] = env
        self.turns.append(record)

        self.history.append(
            "R%-2d %-6s %-8s %s%s%s"
            % (
                round_no,
                agent,
                env.verdict,
                record.ingest or "no issue changes",
                " | %d file(s) written" % len(patch_log) if patch_log else "",
                " | gate %s" % ("PASS" if gate.ok else "FAIL") if self.workspace.gate else "",
            )
        )
        self.emit(
            "turn_done",
            round=round_no,
            agent=agent,
            verdict=env.verdict,
            confidence=env.confidence,
            parse_ok=env.parse_ok,
            issues=[i.id for i in env.issues],
            resolves=list(env.resolves),
            ingest=record.ingest,
            digest=digest_after,
            gate_ok=gate.ok,
            message=env.message,
            notes=env.notes,
        )
        self._save()
        return record

    # -- arbitration ------------------------------------------------------
    def arbitrate(self, issue_id: str, round_no: int) -> None:
        issue = self.state.issues.get(issue_id)
        if issue is None:
            return
        self.emit("arbitration_start", round=round_no, issue=issue_id, title=issue.title)

        positions: Dict[str, str] = {}
        for agent in self.config.order():
            directive = prompts.ARBITRATION_POSITION_DIRECTIVE.format(
                issue_id=issue.id, issue_title=issue.title, rounds=issue.rounds_open
            )
            record = self.take_turn(agent, round_no, directive=directive, ingest=False)
            positions[agent] = record.envelope.message or record.error or "(no position given)"
            self.last_envelope[agent] = record.envelope

        decider = self.decider()
        rendered = "\n\n".join(
            "--- %s's final position ---\n%s" % (name, text) for name, text in positions.items()
        )
        directive = prompts.ARBITRATION_RULING_DIRECTIVE.format(
            issue_id=issue.id, issue_title=issue.title, positions=rendered
        )
        record = self.take_turn(decider, round_no, directive=directive, ingest=True)
        ruling = record.envelope.meta.get("ruling")
        if isinstance(ruling, dict):
            text = "%s — %s" % (ruling.get("decision", "ruled"), ruling.get("rationale", ""))
        else:
            text = record.envelope.summary or record.envelope.message[:300]

        if record.error or not record.envelope.parse_ok or not text.strip():
            reason = record.error or "the decider gave no readable ruling"
            self.state.record_failed_arbitration(issue.id, reason)
            self.history.append("R%-2d ARBITRATION on %s FAILED: %s" % (round_no, issue.id, reason[:70]))
            self.emit("arbitration_failed", round=round_no, issue=issue.id, decider=decider, reason=reason)
            return

        self.state.record_arbitration(issue.id, decider, text, round_no)
        self.history.append("R%-2d ARBITRATION on %s -> %s (by %s)" % (round_no, issue.id, text[:80], decider))
        self.emit("arbitration_done", round=round_no, issue=issue.id, decider=decider, ruling=text)

    # -- the loop ---------------------------------------------------------
    def run(self) -> SessionResult:
        cfg = self.config
        order = cfg.order()
        # Children inherit this, and the CLI refuses to start a session when it
        # is set. An agent that invokes duet spawns another pair of agents, and
        # the outer turn then waits on the whole nested session: in a real run
        # that cost a 30-minute adapter timeout and produced no envelope.
        os.environ["DUET_SESSION"] = self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.emit(
            "session_start",
            session=self.session_id,
            task=cfg.task,
            root=cfg.root,
            agents={a.name: self.adapters[a.name].describe() for a in cfg.agents},
            order=order,
            gate=cfg.gate,
            max_rounds=cfg.max_rounds,
        )

        status, reason = STATUS_EXHAUSTED, "reached the %d-round limit" % cfg.max_rounds
        round_no = 0
        errors: Dict[str, int] = {name: 0 for name in cfg.agent_names}

        try:
            for round_no in range(1, cfg.max_rounds + 1):
                agent = order[(round_no - 1) % len(order)]
                peer = self.state.peer_of(agent)
                digest = self.workspace.digest()
                gate = self.gate_for(digest)

                directive = self.pending_directive.pop(agent, "")
                if not directive:
                    peer_signoff = self.state.signoffs.get(peer)
                    if peer_signoff and peer_signoff.digest == digest and gate.ok and not self.state.blocking_issues():
                        directive = prompts.FINAL_CHECK_DIRECTIVE

                record = self.take_turn(agent, round_no, directive=directive)

                if record.error:
                    errors[agent] += 1
                    if errors[agent] >= 2:
                        status, reason = STATUS_ERROR, "%s failed twice: %s" % (agent, record.error)
                        break
                    continue
                errors[agent] = 0

                if not record.envelope.parse_ok:
                    self.pending_directive[agent] = (
                        "Your last reply had no JSON envelope, so the harness could not "
                        "record your verdict or your issues — your peer saw only prose. "
                        "Re-send your position this turn and end the reply with the "
                        "envelope.\n\n" + prompts.NORMAL_DIRECTIVE
                    )

                decision = self.state.decide(record.digest, (record.gate or gate).ok, cfg.on_blocked)
                if decision.kind == "consensus":
                    status, reason = STATUS_CONSENSUS, decision.reason
                    break
                if decision.kind == "blocked":
                    status, reason = STATUS_BLOCKED, decision.reason
                    break
                if decision.kind == "arbitrate":
                    self.emit("decision", decision=decision.kind, reason=decision.reason)
                    self.arbitrate(decision.issue_id, round_no)
                    continue
                if decision.kind == "stalled":
                    self.emit("decision", decision=decision.kind, reason=decision.reason)
                    stall = prompts.STALL_DIRECTIVE.format(rounds=self.state.stall_rounds)
                    self.pending_directive[agent] = stall
                    self.pending_directive[peer] = stall
                    self.state.stall_rounds = 0
        except KeyboardInterrupt:
            status, reason = STATUS_INTERRUPTED, "interrupted by the user"

        digest = self.workspace.digest()
        gate = self.gate_for(digest)
        result = SessionResult(
            status=status,
            rounds=round_no,
            digest=digest,
            reason=reason,
            gate=gate,
            session_dir=str(self.session_dir),
        )
        os.environ.pop("DUET_SESSION", None)
        result.report = self.write_report(result)
        self._save(result)
        if status == STATUS_CONSENSUS and cfg.commit:
            self.commit(result)
        self.emit("session_end", status=status, reason=reason, rounds=round_no, digest=digest, report=result.report)
        return result

    # -- persistence ------------------------------------------------------
    def _save(self, result: Optional[SessionResult] = None) -> None:
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "session_id": self.session_id,
                "config": self.config.to_dict(),
                "state": self.state.to_dict(),
                "history": self.history,
                "adapters": {name: a.state() for name, a in self.adapters.items()},
                "last_envelope": {k: v.to_dict() for k, v in self.last_envelope.items()},
                "result": (
                    {"status": result.status, "rounds": result.rounds, "reason": result.reason}
                    if result
                    else None
                ),
            }
            (self.session_dir / "state.json").write_text(
                json.dumps(payload, indent=2, default=str), encoding="utf-8"
            )
            (self.session_dir / "transcript.md").write_text(self.transcript(), encoding="utf-8")
        except OSError:
            pass

    def transcript(self) -> str:
        lines = ["# duet session %s" % self.session_id, "", "## Task", "", self.config.task, ""]
        if self.config.acceptance:
            lines += ["## Acceptance", "", self.config.acceptance, ""]
        for record in self.turns:
            env = record.envelope
            lines.append("---")
            lines.append(
                "## Round %d — %s (%s) — %s" % (record.round, record.agent, record.role, env.verdict)
            )
            lines.append("")
            if record.error:
                lines += ["> backend error: %s" % record.error, ""]
            if env.message:
                lines += [env.message, ""]
            if env.issues:
                lines.append("**Issues raised:** " + ", ".join("`%s` (%s)" % (i.id, i.severity) for i in env.issues))
            if env.resolves:
                lines.append("**Claims fixed:** " + ", ".join("`%s`" % i for i in env.resolves))
            if record.patch_log:
                lines.append("**Files written:** " + "; ".join(record.patch_log))
            if record.gate and not record.gate.skipped:
                lines.append("**Gate:** %s" % ("passed" if record.gate.ok else "FAILED"))
            lines.append("**Workspace:** `%s`" % record.digest[:8])
            lines.append("")
        return "\n".join(lines)

    def write_report(self, result: SessionResult) -> str:
        lines = [
            "# duet report — %s" % self.session_id,
            "",
            "**Outcome:** %s — %s" % (result.status, result.reason),
            "**Rounds:** %d of %d" % (result.rounds, self.config.max_rounds),
            "**Workspace state:** `%s`" % result.digest[:8],
            "",
            "## Sign-off",
            "",
        ]
        for name in self.config.agent_names:
            verdict = self.state.last_verdict.get(name, "—")
            signoff = self.state.signoffs.get(name)
            mark = "yes" if signoff and signoff.digest == result.digest else "no"
            lines.append("- **%s** (%s): last verdict `%s`, signed off on this state: **%s**"
                         % (name, self.adapters[name].describe(), verdict, mark))
        if result.gate and not result.gate.skipped:
            lines += ["", "## Acceptance gate", "", "```", result.gate.render(1500), "```"]
        open_issues = self.state.open_issues()
        lines += ["", "## Issues", ""]
        if not self.state.issues:
            lines.append("No issues were raised.")
        else:
            for issue in self.state.issues.values():
                lines.append(
                    "- `%s` **%s** (%s, from %s) — %s%s"
                    % (issue.id, issue.title, issue.severity, issue.raised_by, issue.status,
                       ": " + issue.resolution if issue.resolution else "")
                )
        if self.state.arbitrations:
            lines += ["", "## Arbitration rulings", ""]
            for item in self.state.arbitrations:
                lines.append("- round %s, `%s` decided by %s: %s"
                             % (item.get("round"), item.get("issue_id"), item.get("decider"), item.get("ruling")))
        if result.status != STATUS_CONSENSUS:
            lines += ["", "## What is left", ""]
            reasons = self.state.why_not_done(result.digest, bool(result.gate and result.gate.ok))
            lines += ["- %s" % r for r in reasons] or ["- nothing recorded"]
            if open_issues:
                lines.append("")
        lines += ["", "## Timeline", "", "```", "\n".join(self.history) or "(no turns)", "```", ""]
        path = self.session_dir / "report.md"
        try:
            path.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            return ""
        return str(path)

    def commit(self, result: SessionResult) -> None:
        if not self.workspace.is_git_repo:
            return
        message = "duet: %s\n\nBoth agents signed off on workspace %s after %d rounds.\nSession: %s\n" % (
            (self.config.task.strip().splitlines() or ["work"])[0][:60],
            result.digest[:8],
            result.rounds,
            self.session_id,
        )
        self.workspace._git("add", "-A")
        code, out = self.workspace._git("commit", "-m", message)
        self.emit("commit", ok=code == 0, output=out.strip()[:400])

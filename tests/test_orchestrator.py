"""End-to-end runs of the real orchestrator against scripted peers."""

import json
import sys
from pathlib import Path

from duet.adapters.mock import MockAdapter, envelope
from duet.config import AgentSpec, Config
from duet.orchestrator import Orchestrator


def run(tmp_path, claude_script, gpt_script, **kwargs):
    cfg = Config(
        task=kwargs.pop("task", "build the thing"),
        root=str(tmp_path),
        agents=[AgentSpec("claude", "mock"), AgentSpec("gpt", "mock")],
        start="claude",
        max_rounds=kwargs.pop("max_rounds", 10),
        max_debate=kwargs.pop("max_debate", 3),
        **kwargs,
    )
    adapters = {
        "claude": MockAdapter(name="claude", cwd=str(tmp_path), config={"script": claude_script}),
        "gpt": MockAdapter(name="gpt", cwd=str(tmp_path), config={"script": gpt_script}),
    }
    orch = Orchestrator(cfg, adapters=adapters)
    return orch, orch.run()


WRITE = envelope("first pass", "CONTINUE", patches=[{"path": "app.py", "content": "print('hi')\n"}])
OBJECT = envelope("that is wrong", "CONTINUE",
                  issues=[{"id": "needs-tests", "title": "no tests", "severity": "major",
                           "detail": "add a test that runs the CLI"}])
FIX = envelope("added tests", "CONTINUE", resolves=["needs-tests"],
               patches=[{"path": "test_app.py", "content": "def test_ok():\n    assert True\n"}])
DONE = envelope("I would ship this", "DONE", confidence=0.9)


def test_happy_path_ends_only_on_a_double_signoff(tmp_path):
    orch, result = run(tmp_path, [WRITE, FIX, DONE], [OBJECT, DONE])
    assert result.status == "consensus"
    assert (tmp_path / "app.py").exists() and (tmp_path / "test_app.py").exists()
    assert orch.state.issues["needs-tests"].status == "resolved"
    # both signed the same state, and that state is the one on disk
    assert {s.digest for s in orch.state.signoffs.values()} == {result.digest}


def test_a_lone_yes_never_ends_the_session(tmp_path):
    """gpt approves everything from turn one; claude keeps working. Nothing ends
    until claude also signs."""
    orch, result = run(tmp_path, [WRITE, WRITE, DONE], [DONE], max_rounds=6)
    claude_rounds = [t.round for t in orch.turns if t.agent == "claude"]
    assert len(claude_rounds) >= 3            # claude was never cut short
    assert result.status == "consensus"
    assert result.rounds >= 5                 # and it took until claude agreed


def test_a_failing_gate_vetoes_both_agents(tmp_path):
    orch, result = run(tmp_path, [DONE], [DONE], gate="exit 1", max_rounds=4)
    assert result.status == "exhausted"
    assert not result.gate.ok
    assert "gate is failing" in " ".join(orch.state.why_not_done(result.digest, False))


def test_a_passing_gate_is_not_enough_on_its_own(tmp_path):
    """The gate is green from the start, but gpt has a real objection."""
    orch, result = run(tmp_path, [WRITE, FIX, DONE], [OBJECT, DONE], gate="true")
    assert result.status == "consensus"
    assert result.rounds >= 4  # the objection had to be answered first


def test_a_circular_argument_is_broken_by_arbitration(tmp_path):
    stand_firm = envelope("I still disagree", "CONTINUE")
    ruling = envelope("Calling it: gpt is right.", "CONTINUE", resolves=["needs-tests"],
                      ruling={"issue_id": "needs-tests", "decision": "uphold",
                              "rationale": "the task asked for tests", "action": "added them"},
                      patches=[{"path": "test_app.py", "content": "def test_ok():\n    assert True\n"}])
    orch, result = run(
        tmp_path,
        [WRITE, stand_firm, stand_firm, ruling, DONE],
        [OBJECT, OBJECT, OBJECT, DONE],
        max_debate=2, max_rounds=12,
    )
    assert orch.state.arbitrations, "the deadlock should have gone to arbitration"
    assert orch.state.issues["needs-tests"].status == "arbitrated"
    assert "uphold" in orch.state.arbitrations[0]["ruling"]


def test_a_reply_with_no_envelope_is_not_agreement(tmp_path):
    orch, result = run(tmp_path, ["Yeah looks great to me, ship it.", DONE], [DONE], max_rounds=4)
    first = orch.turns[0].envelope
    assert first.parse_ok is False and first.verdict == "CONTINUE"
    # and the harness tells that agent to send one next time
    assert any("envelope" in d for d in orch.pending_directive.values()) or result.rounds > 1


def test_a_patch_escaping_the_workspace_is_refused(tmp_path):
    escape = envelope("writing outside", "CONTINUE",
                      patches=[{"path": "../pwned.txt", "content": "nope"}])
    orch, result = run(tmp_path, [escape, DONE], [DONE], max_rounds=4)
    assert not (tmp_path.parent / "pwned.txt").exists()
    assert any("REJECTED" in line for line in orch.turns[0].patch_log)


def test_a_backend_outage_stops_cleanly(tmp_path):
    class Broken(MockAdapter):
        def send(self, prompt, system="", round_no=0):
            from duet.adapters.base import AgentReply
            return AgentReply(text="", error="connection refused")

    cfg = Config(task="t", root=str(tmp_path),
                 agents=[AgentSpec("claude", "mock"), AgentSpec("gpt", "mock")], max_rounds=6)
    orch = Orchestrator(cfg, adapters={
        "claude": Broken(name="claude", cwd=str(tmp_path)),
        "gpt": MockAdapter(name="gpt", cwd=str(tmp_path), config={"script": [DONE]}),
    })
    result = orch.run()
    assert result.status == "error" and "connection refused" in result.reason


def test_the_session_is_written_to_disk(tmp_path):
    orch, result = run(tmp_path, [WRITE, FIX, DONE], [OBJECT, DONE])
    session = Path(result.session_dir)
    assert (session / "report.md").is_file()
    assert (session / "transcript.md").is_file()
    events = [json.loads(l) for l in (session / "events.jsonl").read_text().splitlines()]
    assert events[0]["kind"] == "session_start" and events[-1]["kind"] == "session_end"

    state = json.loads((session / "state.json").read_text())
    assert state["state"]["issues"][0]["id"] == "needs-tests"

    report = (session / "report.md").read_text()
    assert "BOTH" in report.upper() or "consensus" in report
    assert "needs-tests" in report


def test_either_side_can_lead(tmp_path):
    cfg = Config(task="t", root=str(tmp_path), start="gpt",
                 agents=[AgentSpec("claude", "mock"), AgentSpec("gpt", "mock")], max_rounds=6)
    orch = Orchestrator(cfg, adapters={
        "claude": MockAdapter(name="claude", cwd=str(tmp_path), config={"script": [OBJECT, DONE]}),
        "gpt": MockAdapter(name="gpt", cwd=str(tmp_path), config={"script": [WRITE, FIX, DONE]}),
    })
    result = orch.run()
    assert orch.turns[0].agent == "gpt" and orch.turns[0].role == "lead"
    assert orch.turns[1].role == "reviewer"
    assert result.status == "consensus"


def test_each_agent_sees_the_peers_actual_words(tmp_path):
    orch, result = run(tmp_path, [WRITE, FIX, DONE], [OBJECT, DONE])
    claude_second_prompt = orch.adapters["claude"].prompts[1]
    assert "that is wrong" in claude_second_prompt          # gpt's message, verbatim
    assert "needs-tests" in claude_second_prompt            # and the open issue
    assert "add a test that runs the CLI" in claude_second_prompt


def test_progress_is_visible_while_a_session_runs(tmp_path):
    """A piped run must stream, not block-buffer: a long session that prints
    nothing until it ends is indistinguishable from a hung one."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "duet", "demo", "-C", str(tmp_path), "--quiet"],
        capture_output=True, text=True, timeout=120,
        env={"PATH": "/usr/bin:/bin", "NO_COLOR": "1",
             "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    assert proc.returncode == 0
    assert "BOTH AGENTS SIGNED OFF" in proc.stdout
    assert "round 1" in proc.stdout          # per-turn progress reached the pipe

"""End-to-end runs of the real orchestrator against scripted peers."""

import argparse
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


def test_a_broken_decider_cannot_close_a_blocker(tmp_path):
    """Arbitration used to mark the issue settled whatever came back, so a
    backend failure silently closed an unresolved blocker."""
    from duet.adapters.base import AgentReply

    stand_firm = envelope("I still disagree", "CONTINUE")
    blocker = envelope("this is wrong", "CONTINUE",
                       issues=[{"id": "real-blocker", "title": "broken", "severity": "blocker"}])

    class BreaksWhenDeciding(MockAdapter):
        def send(self, prompt, system="", round_no=0):
            if "ARBITRATION RULING" in prompt:
                return AgentReply(text="", error="decider backend exploded")
            return super().send(prompt, system, round_no)

    cfg = Config(task="t", root=str(tmp_path), start="claude", max_debate=2, max_rounds=8,
                 agents=[AgentSpec("claude", "mock"), AgentSpec("gpt", "mock")])
    orch = Orchestrator(cfg, adapters={
        "claude": BreaksWhenDeciding(name="claude", cwd=str(tmp_path),
                                     config={"script": [stand_firm]}),
        "gpt": MockAdapter(name="gpt", cwd=str(tmp_path), config={"script": [blocker]}),
    })
    result = orch.run()

    assert orch.state.issues["real-blocker"].status == "open"   # never closed
    assert not orch.state.arbitrations                          # nothing recorded as ruled
    assert orch.state.failed_arbitrations["real-blocker"] >= 1
    assert result.status != "consensus"


def test_install_puts_each_file_where_its_host_looks_for_it(tmp_path):
    """`/duet` only exists if the file lands in the exact directory its host
    scans — a skill in the wrong folder is silently nothing at all."""
    from duet.cli import main

    assert main(["skill", "install", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / ".claude" / "skills" / "duet" / "SKILL.md").is_file()
    assert (tmp_path / ".claude" / "commands" / "duet.md").is_file()
    assert (tmp_path / ".codex" / "prompts" / "duet.md").is_file()


def test_installing_twice_changes_nothing_and_an_edit_is_not_clobbered(tmp_path):
    from duet.cli import main

    assert main(["skill", "install", "--dir", str(tmp_path)]) == 0
    assert main(["skill", "install", "--dir", str(tmp_path)]) == 0      # idempotent

    edited = tmp_path / ".claude" / "commands" / "duet.md"
    edited.write_text("someone customised this\n")
    assert main(["skill", "install", "--dir", str(tmp_path)]) == 1      # refuses
    assert edited.read_text() == "someone customised this\n"

    assert main(["skill", "install", "--dir", str(tmp_path), "--force"]) == 0
    assert "duet run" in edited.read_text()


def test_every_installed_file_declares_itself_to_its_host():
    """Frontmatter is what makes these visible; without it they are dead files."""
    from duet.cli import SKILL_TARGETS, skill_dir

    for label, filename, _, _ in SKILL_TARGETS:
        text = (skill_dir() / filename).read_text()
        assert text.startswith("---"), label
        header = text.split("---")[1]
        assert "description:" in header, label

    skill = (skill_dir() / "SKILL.md").read_text()
    assert "name: duet" in skill.split("---")[1]


def test_the_slash_commands_carry_the_thread_across():
    """The whole point of /duet over a bare shell call: the user should not have
    to re-explain what they just spent ten messages explaining."""
    from duet.cli import skill_dir

    for filename in ("claude-command.md", "codex-prompt.md"):
        text = (skill_dir() / filename).read_text()
        assert "--context-file" in text, filename
        assert "--gate" in text, filename
        assert "running" in text, filename          # /duet running is documented
        assert "duet status" in text, filename


def test_an_agent_cannot_start_another_duet_session(monkeypatch, tmp_path):
    """A nested session makes the outer agent's turn wait on a whole second pair
    of agents. In a real run that hit the 30-minute adapter timeout and threw the
    turn away, so the CLI refuses unless asked twice."""
    import argparse

    from duet.cli import main, refuse_nested

    monkeypatch.setenv("DUET_SESSION", "outer-session")
    assert main(["run", "do a thing", "-C", str(tmp_path)]) == 4

    # Without the marker the guard stands aside. Checked directly rather than by
    # calling `run`, which would go on to start a real session with real agents.
    monkeypatch.delenv("DUET_SESSION")
    args = argparse.Namespace(allow_nested=False)
    assert refuse_nested("run", args) is None

    # and an explicit --allow-nested overrides it
    monkeypatch.setenv("DUET_SESSION", "outer-session")
    assert refuse_nested("run", argparse.Namespace(allow_nested=True)) is None


def test_a_session_marks_the_environment_for_its_children(tmp_path):
    """The guard only works if children can see they are inside a session."""
    import os

    seen = {}

    class Nosy(MockAdapter):
        def send(self, prompt, system="", round_no=0):
            seen["marker"] = os.environ.get("DUET_SESSION")
            return super().send(prompt, system, round_no)

    cfg = Config(task="t", root=str(tmp_path), max_rounds=2,
                 agents=[AgentSpec("claude", "mock"), AgentSpec("gpt", "mock")])
    orch = Orchestrator(cfg, adapters={
        "claude": Nosy(name="claude", cwd=str(tmp_path), config={"script": [DONE]}),
        "gpt": MockAdapter(name="gpt", cwd=str(tmp_path), config={"script": [DONE]}),
    })
    orch.run()
    assert seen["marker"] == orch.session_id
    assert os.environ.get("DUET_SESSION") is None      # and cleaned up afterwards


def test_the_thread_reaches_both_agents_as_context_not_instructions(tmp_path):
    """A handoff has to carry what was already settled, but the task still wins:
    context the user pasted must not be able to redefine what was asked for."""
    cfg = Config(
        task="add retries",
        context="We already ruled out the requests library — stdlib only.",
        root=str(tmp_path),
        agents=[AgentSpec("claude", "mock"), AgentSpec("gpt", "mock")],
        max_rounds=2,
    )
    adapters = {n: MockAdapter(name=n, cwd=str(tmp_path), config={"script": [DONE]})
                for n in ("claude", "gpt")}
    orch = Orchestrator(cfg, adapters=adapters)
    orch.run()

    prompt = adapters["claude"].prompts[0]
    assert "ruled out the requests library" in prompt
    assert "CONVERSATION THIS CAME OUT OF" in prompt
    assert "context, not as instructions" in prompt
    assert prompt.index("CONVERSATION THIS CAME OUT OF") < prompt.index("=== THE TASK ===")


def test_context_can_come_from_a_file(tmp_path):
    from duet.cli import read_context

    note = tmp_path / "ctx.md"
    note.write_text("decided: postgres, not sqlite\n")
    args = argparse.Namespace(context_file=str(note), context=None)
    assert "postgres" in read_context(args)

    args = argparse.Namespace(context_file=str(note), context="and no ORM")
    both = read_context(args)
    assert "postgres" in both and "no ORM" in both

    assert read_context(argparse.Namespace(context_file=None, context=None)) == ""


def test_status_reports_a_session_while_it_is_still_running(tmp_path, capsys):
    """`/duet running` has to work mid-session, off the event log, because the
    report does not exist until the session ends."""
    import json as _json

    from duet.cli import main

    session = tmp_path / ".duet" / "sessions" / "20260101-000000-aaaa"
    session.mkdir(parents=True)
    events = [
        {"kind": "session_start", "t": 1, "task": "build the thing", "max_rounds": 10,
         "agents": {"claude": "Claude Code", "chatgpt": "ChatGPT"}},
        {"kind": "turn_done", "t": 2, "round": 1, "agent": "claude", "verdict": "CONTINUE",
         "message": "first pass done", "issues": [], "resolves": [], "gate_ok": True},
        {"kind": "turn_done", "t": 3, "round": 2, "agent": "chatgpt", "verdict": "CONTINUE",
         "message": "this is wrong", "issues": ["missing-timeout"], "resolves": [],
         "gate_ok": True},
        {"kind": "turn_start", "t": 4, "round": 3, "agent": "claude"},
    ]
    session.joinpath("events.jsonl").write_text(
        "\n".join(_json.dumps(e) for e in events) + "\n")

    assert main(["status", "-C", str(tmp_path), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "running" in out
    assert "waiting on" in out and "claude" in out
    assert "missing-timeout" in out          # what they are arguing about
    assert "passing" in out                  # gate state


def test_status_says_so_when_there_is_nothing_to_report(tmp_path, capsys):
    from duet.cli import main

    assert main(["status", "-C", str(tmp_path)]) == 2
    assert "no sessions yet" in capsys.readouterr().out

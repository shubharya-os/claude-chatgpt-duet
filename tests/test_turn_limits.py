"""The turn clock and the network: what a session does when time or DNS runs out.

Written against a live session that ended in ERROR twice over while making real
progress. First the lead hit the adapter's hard-coded 1800s limit in rounds 1 and
3 — 1,500 lines of implementation, then 1,060 lines of tests, both on disk — and
"failed twice" ended the session. The limit could not be raised from any command
line, so recovering meant hand-editing state.json. Then, resumed, the machine
lost DNS for a few minutes: every turn failed instantly with "Can't reach the API
server", the second failure ended the session again, and rounds 4–6 of the budget
were spent on turns that never reached a model.

Nothing this fix introduced is imported at the top of this file, on purpose. The
harness proves these tests catch the bug by replaying them against the code as it
was before the session, and a module-level `from duet.adapters.base import
transient_failure` would make every test in the file fail there with an
ImportError — a red replay that proves only that a name is new. Imported inside
the tests that need them, the behavioural tests below run on the old code and
fail on what they actually assert: a cut-off turn ending the session, a round
spent on a turn that never happened, an unrecognised `--turn-timeout`.
"""

import json
import sys
from pathlib import Path

import pytest

from duet.adapters.base import AgentReply
from duet.adapters.claude_code import ClaudeCodeAdapter
from duet.adapters.mock import MockAdapter, envelope
from duet.cli import build_parser, main
from duet.config import AgentSpec, Config
from duet.orchestrator import Orchestrator

DNS = "API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)"

WRITE = envelope("first pass", "CONTINUE",
                 patches=[{"path": "app.py", "content": "print('hi')\n"}])
DONE = envelope("I would ship this", "DONE", confidence=0.9)


class Scripted(MockAdapter):
    """A mock whose script may hold callables: step(adapter) -> AgentReply.

    The failures this file is about are not envelopes — they are a turn that was
    stopped, or one that never reached a model — so the script has to be able to
    say something other than "here is what the agent replied".
    """

    def send(self, prompt, system="", round_no=0):
        self.prompts.append(prompt)
        self.systems.append(system)
        step = self.script[min(self.turns, len(self.script) - 1)]
        self.turns += 1
        if callable(step):
            return step(self)
        return AgentReply(text=step, meta={"backend": "mock", "turn": self.turns})


def cut_off(seconds=1800, writes=None):
    """A turn the harness stopped at its limit, having written `writes` first.

    Spelled out rather than built with `human_duration`, so this helper is exactly
    what the old adapters produced plus the `timed_out` flag — on code that does
    not know that flag it is an ignored dict key, and the test still runs.
    """
    def step(adapter):
        for rel, body in (writes or {}).items():
            path = Path(adapter.cwd) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return AgentReply(
            text="",
            error="claude timed out after %ds" % seconds,
            meta={"backend": "mock", "timed_out": True, "timeout_s": seconds},
        )
    return step


def failure(message):
    def step(adapter):
        return AgentReply(text="", error=message, meta={"backend": "mock"})
    return step


def session(tmp_path, claude_script, gpt_script, **kwargs):
    cfg = Config(
        task=kwargs.pop("task", "build the thing"),
        root=str(tmp_path),
        agents=[AgentSpec("claude", "mock"), AgentSpec("gpt", "mock")],
        start="claude",
        max_rounds=kwargs.pop("max_rounds", 6),
        **kwargs,
    )
    adapters = {
        "claude": Scripted(name="claude", cwd=str(tmp_path), config={"script": claude_script}),
        "gpt": Scripted(name="gpt", cwd=str(tmp_path), config={"script": gpt_script}),
    }
    orch = Orchestrator(cfg, adapters=adapters)
    return orch, orch.run()


def events(result, kind):
    path = Path(result.session_dir) / "events.jsonl"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            event = json.loads(line)
            if event.get("kind") == kind:
                out.append(event)
    return out


# -- 1. the limit is settable, saved, and stated ---------------------------
def parse(*argv):
    return build_parser().parse_args(list(argv))


@pytest.mark.parametrize("command", ["run", "fix", "add", "refactor", "plan", "build", "resume"])
def test_every_session_command_takes_a_turn_timeout(command):
    """It could not be set anywhere, so recovering a timed-out session meant
    editing .duet/sessions/<id>/state.json by hand."""
    args = parse(command, "something", "--turn-timeout", "45")
    assert args.turn_timeout == 45


def test_the_turn_timeout_reaches_the_config_in_seconds(tmp_path):
    from duet.cli import build_config

    args = parse("fix", "the bug", "-C", str(tmp_path), "--turn-timeout", "50", "--no-gate")
    assert build_config(args).turn_timeout == 3000


def test_a_turn_timeout_under_a_minute_is_refused(tmp_path):
    from duet.cli import build_config

    with pytest.raises(SystemExit):
        build_config(parse("run", "x", "-C", str(tmp_path), "--turn-timeout", "0", "--no-gate"))


def test_the_limit_reaches_the_adapters_that_enforce_it(tmp_path):
    cfg = Config(task="t", root=str(tmp_path), turn_timeout=2700,
                 agents=[AgentSpec("claude", "claude-code"), AgentSpec("gpt", "codex-cli")])
    orch = Orchestrator(cfg)
    assert [orch.adapters[n].timeout for n in ("claude", "gpt")] == [2700, 2700]
    assert orch.turn_budget("claude") == 2700


def test_an_adapter_given_a_limit_passes_it_to_the_cli(tmp_path):
    agent = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path))
    agent.set_turn_timeout(3600)
    assert agent.timeout == 3600 and agent.config["timeout"] == 3600


def test_both_agents_are_told_their_time_budget(tmp_path):
    """Nobody told them how long a turn could take, so the lead tried to do
    everything in one."""
    orch, _ = session(tmp_path, [DONE], [DONE], turn_timeout=1200, max_rounds=2)
    for name in ("claude", "gpt"):
        prompt = orch.adapters[name].prompts[0]
        assert "TIME BUDGET" in prompt
        assert "20 minutes" in prompt
        assert "hand over" in prompt


def test_the_budget_stated_is_the_one_that_will_stop_the_turn(tmp_path):
    """With no --turn-timeout, the number in the prompt is the adapter's own."""
    cfg = Config(task="t", root=str(tmp_path), agents=[AgentSpec("claude", "claude-code"),
                                                      AgentSpec("gpt", "codex-cli")])
    orch = Orchestrator(cfg)
    orch.adapters["claude"].timeout = 600
    assert orch.turn_budget("claude") == 600


def test_the_limit_survives_a_resume(tmp_path):
    """It is saved with the session, so `duet resume` keeps it without the flag."""
    cfg = Config(
        task="build the thing", root=str(tmp_path), turn_timeout=2400, max_rounds=1,
        agents=[AgentSpec("claude", "mock", options={"script": [WRITE]}),
                AgentSpec("gpt", "mock", options={"script": [WRITE]})],
        start="claude",
    )
    first = Orchestrator(cfg)
    first.run()
    state = json.loads((Path(first.session_dir) / "state.json").read_text(encoding="utf-8"))
    assert state["config"]["turn_timeout"] == 2400

    assert main(["resume", "-C", str(tmp_path), "--rounds", "3"]) in (0, 1)
    newest = sorted((tmp_path / ".duet" / "sessions").iterdir())[-1]
    resumed = json.loads((newest / "state.json").read_text(encoding="utf-8"))
    assert resumed["config"]["turn_timeout"] == 2400


def test_resume_can_raise_the_limit(tmp_path):
    """The session that died on its turn limit is exactly the one being resumed."""
    cfg = Config(
        task="build the thing", root=str(tmp_path), turn_timeout=600, max_rounds=1,
        agents=[AgentSpec("claude", "mock", options={"script": [WRITE]}),
                AgentSpec("gpt", "mock", options={"script": [WRITE]})],
        start="claude",
    )
    Orchestrator(cfg).run()
    assert main(["resume", "-C", str(tmp_path), "--rounds", "3",
                 "--turn-timeout", "90"]) in (0, 1)
    newest = sorted((tmp_path / ".duet" / "sessions").iterdir())[-1]
    resumed = json.loads((newest / "state.json").read_text(encoding="utf-8"))
    assert resumed["config"]["turn_timeout"] == 5400


# -- 2. a timeout that made progress is progress ---------------------------
def test_a_timeout_that_changed_the_workspace_does_not_fail_the_session(tmp_path):
    """Two cut-off turns in a row, both leaving work behind. The old rule ended
    the session on the second one: "claude failed twice: claude timed out"."""
    orch, result = session(
        tmp_path,
        [cut_off(writes={"impl.py": "x = 1\n"}),
         cut_off(writes={"test_impl.py": "def test_x():\n    assert True\n"}),
         DONE],
        [DONE],
        max_rounds=6,
    )
    assert result.status != "error", result.reason
    assert (tmp_path / "impl.py").is_file() and (tmp_path / "test_impl.py").is_file()
    cuts = [t for t in orch.turns if t.cut_off]
    assert len(cuts) == 2
    assert all(t.error for t in cuts)          # still reported as what it was


def test_the_peer_is_told_the_previous_turn_was_cut_off(tmp_path):
    orch, result = session(
        tmp_path, [cut_off(writes={"impl.py": "x = 1\n"}), DONE], [DONE], max_rounds=4,
    )
    handover = orch.adapters["gpt"].prompts[0]
    assert "CUT OFF" in handover
    assert "30 minutes" in handover             # the budget it ran out of
    assert "no message and no envelope" in handover
    assert events(result, "turn_cut_off")


def test_a_cut_off_turn_keeps_its_round(tmp_path):
    """It did the work, so it spends the round: the peer answers on round 2."""
    orch, _ = session(tmp_path, [cut_off(writes={"impl.py": "x = 1\n"}), DONE], [DONE],
                      max_rounds=4)
    assert [(t.round, t.agent) for t in orch.turns][:2] == [(1, "claude"), (2, "gpt")]


def test_a_timeout_that_changed_nothing_still_counts_as_a_failure(tmp_path):
    """An agent that burns its whole budget and writes nothing is not progress."""
    orch, result = session(tmp_path, [cut_off(), cut_off(), DONE], [DONE], max_rounds=6)
    assert result.status == "error" and "timed out" in result.reason
    assert not any(t.cut_off for t in orch.turns)


# -- 3. a blip is waited out, not counted ----------------------------------
def test_the_schedule_waits_out_a_few_minutes_of_outage():
    from duet.orchestrator import RETRY_MAX_DELAY, retry_delays

    delays = retry_delays()
    assert delays == sorted(delays) and delays[-1] == RETRY_MAX_DELAY
    assert 180 <= sum(delays) <= 600            # several minutes, not an hour
    assert len(delays) >= 4


@pytest.mark.parametrize("message", [
    DNS,
    "network error: <urlopen error [Errno -3] Temporary failure in name resolution>",
    "Connection reset by peer",
    "HTTP 503: upstream connect error",
    "HTTP 429: Too Many Requests",
    "API Error: Overloaded",
    # The shapes the CLIs actually print. Claude Code puts the status in front of
    # a JSON body and spells nothing out, so a matcher that only reads prose
    # ("too many requests", "service unavailable") misses the rate limit and the
    # bare 5xx the task names — and the turn is charged to the agent instead.
    'API Error: 429 {"type":"error","error":{"type":"rate_limit_error",'
    '"message":"Number of request tokens has exceeded your per-minute rate limit."}}',
    "API Error: 503",
    "API Error: 529",
    "claude produced no output: API Error: 502",
    # The word at the end of a sentence is still the network: the guard below
    # refuses `dns.py`, not `DNS.`
    "the request failed: no route to the API, check your internet or DNS.",
    # codex's half of it: the stream drops and it says only this.
    "ERROR: stream disconnected before completion: error sending request",
])
def test_these_failures_are_worth_retrying(message):
    from duet.adapters.base import transient_failure

    assert transient_failure(message)


@pytest.mark.parametrize("message", [
    "Claude Code is not signed in. Run: claude auth login",
    "the `claude` CLI was not found. Install it with `npm install -g ...`",
    "ERROR: You've hit your usage limit. Try again at Oct 3, 2025 5:00 PM.",
    "the model returned an empty message",
    # A 4xx that is not 429 is the request's fault, and four minutes of waiting
    # will not make the prompt shorter or the key valid.
    "API Error: 400 invalid_request_error: prompt is too long",
    "API Error: 401 authentication_error: invalid x-api-key",
    # A status number that is not a status number. The reply text is searched as
    # well as the error, and agents talk about their own test files.
    "AssertionError in tests/test_error_503_handling.py line 12",
    # And the same mistake one tier up, in the words rather than the numbers.
    # When the CLI reports is_error, the reply's error *is* the agent's own
    # message, truncated — so a pair working on networking code hands this
    # matcher a file name, and a real failure that should have been reported at
    # once would be re-sent five more times at a full turn's cost each.
    "AssertionError in tests/test_dns_resolver.py line 12",
    "I rewrote dns_cache.py and the gate is green; then the CLI exited 1",
    'Traceback: File "app/dns.py", line 4, in resolve',
    "the backoff lives in overloaded_queue.py, and it exited 1",
    # A durable marker glued into an identifier is still durable: this is what
    # the OpenAI backend returns when the account is out of credit, and waiting
    # four minutes does not buy any.
    'HTTP 429: {"error":{"code":"insufficient_quota"}}',
])
def test_these_failures_are_real_and_reported_at_once(message):
    from duet.adapters.base import transient_failure

    assert not transient_failure(message)


@pytest.mark.parametrize("seconds,expected", [
    (1800, "30 minutes"), (60, "60 seconds"), (3600, "1 hour"),
    (3660, "1 hour 1 minute"), (5400, "1 hour 30 minutes"), (7200, "2 hours"),
])
def test_the_budget_reads_as_a_duration(seconds, expected):
    """It goes into the agents' prompt and into `--help`, so "1 hour 1 minutes"
    is a typo two models and every user would read."""
    from duet.adapters.base import human_duration

    assert human_duration(seconds) == expected


def test_a_bare_status_code_blip_is_waited_out_too(tmp_path, monkeypatch):
    """The end of the same hole: `API Error: 429` with a JSON body used to be
    reported as the agent's own failure, spending the round and counting toward
    "failed twice"."""
    from duet.orchestrator import retry_delays

    waits = []
    monkeypatch.setattr("duet.orchestrator.SLEEP", waits.append)
    orch, result = session(
        tmp_path,
        [failure('API Error: 429 {"type":"error","error":'
                 '{"type":"rate_limit_error","message":"per-minute rate limit"}}'),
         WRITE, DONE],
        [DONE],
        max_rounds=4,
    )
    assert waits == retry_delays()[:1]
    assert result.status == "consensus"
    assert orch.turns[0].meta["attempts"] == 2
    assert events(result, "backend_retry")[0]["reason"] == "http 429"


def test_a_blip_is_retried_with_backoff_and_the_turn_still_happens(tmp_path, monkeypatch):
    from duet.orchestrator import retry_delays

    waits = []
    monkeypatch.setattr("duet.orchestrator.SLEEP", waits.append)
    orch, result = session(
        tmp_path,
        [failure(DNS), failure(DNS), WRITE, DONE],   # the script does not advance
        [DONE],                                      # per round, but per send
        max_rounds=4,
    )
    assert waits == retry_delays()[:2]
    assert result.status == "consensus"
    # one turn, not three: the two blips did not reach the transcript as turns
    assert [t.round for t in orch.turns if t.agent == "claude"] == [1, 3]
    assert orch.turns[0].meta["attempts"] == 3
    assert len(events(result, "backend_retry")) == 2


def test_a_blip_that_outlasts_the_backoff_does_not_spend_the_round(tmp_path):
    """DNS was down for minutes: rounds 4, 5 and 6 were spent on turns that never
    reached a model, and `duet resume` had nothing left to resume into."""
    cfg = Config(
        task="build the thing", root=str(tmp_path), max_rounds=6,
        agents=[AgentSpec("claude", "mock", options={"script": [WRITE, DONE]}),
                AgentSpec("gpt", "mock", options={"script": [DONE]})],
        start="claude",
    )
    orch = Orchestrator(cfg)
    orch.adapters["claude"] = Scripted(name="claude", cwd=str(tmp_path),
                                       config={"script": [WRITE, failure(DNS)]})
    orch.adapters["gpt"] = Scripted(name="gpt", cwd=str(tmp_path), config={"script": [DONE]})
    result = orch.run()

    assert result.status == "error" and "ENOTFOUND" in result.reason
    # round 1 (claude) and round 2 (gpt) happened; the failures after them did not
    assert orch.round_no == 2
    state = json.loads((Path(result.session_dir) / "state.json").read_text(encoding="utf-8"))
    assert state["rounds"] == 2
    never = events(result, "turn_never_happened")
    # Several goes before giving up, not one — the exact schedule is pinned by
    # test_the_schedule_waits_out_a_few_minutes_of_outage. Spelled without
    # `retry_delays` so this test, the one that carries the round-budget claim,
    # runs on the old code and fails on `round_no` rather than on an import.
    assert len(never) == 2 and never[0]["attempts"] >= 5


def test_a_turn_that_never_happened_keeps_the_directive_it_was_owed(tmp_path):
    """The stall nudge and the "send an envelope" correction are owed to an
    agent, not to a round number."""
    orch, _ = session(
        tmp_path,
        ["prose with no envelope at all", failure(DNS)],
        [DONE],
        max_rounds=4,
    )
    assert "envelope" in orch.pending_directive.get("claude", "")


def test_a_real_failure_is_not_retried_at_all(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr("duet.orchestrator.SLEEP", waits.append)
    signed_out = "Claude Code is not signed in. Run: claude auth login"
    orch, result = session(tmp_path, [failure(signed_out)], [DONE], max_rounds=6)
    assert waits == []
    assert result.status == "error" and "not signed in" in result.reason
    # two failed turns, and both of them charged a round, exactly as before
    assert orch.round_no >= 1
    assert not any(t.never_happened or t.cut_off for t in orch.turns)


def test_a_real_failure_that_merely_names_a_file_is_not_retried(tmp_path, monkeypatch):
    """The cost of guessing "transient" wrongly, end to end.

    Retrying does not re-send a banner — it re-runs the whole turn, up to half
    an hour of it, five more times. So a failure that only *mentions* the
    network, because the pair is working on networking code, must be reported
    the moment it happens, exactly as it was before any of this.
    """
    waits = []
    monkeypatch.setattr("duet.orchestrator.SLEEP", waits.append)
    crash = 'Traceback (most recent call last): File "app/dns.py", line 4 — SyntaxError'
    orch, result = session(tmp_path, [failure(crash)], [DONE], max_rounds=6)
    assert waits == []
    assert result.status == "error" and "dns.py" in result.reason
    # `getattr` so this reads as a regression guard on code that has the flag and
    # as a plain description of today's behaviour on code that does not: it is
    # not one of the tests that carry the bug, and it should not go red in the
    # replay on the name of an attribute.
    assert not any(getattr(t, "never_happened", False) for t in orch.turns)
    assert orch.adapters["claude"].turns == 2       # two turns, not twelve sends


def test_the_round_budget_still_ends_the_session(tmp_path):
    """Giving rounds back must not let a session run forever."""
    orch, result = session(tmp_path, [WRITE], [WRITE], max_rounds=3)
    assert result.status == "exhausted" and result.rounds == 3

from duet.consensus import DebateState
from duet.protocol import parse_envelope


def env(agent, round_no, body):
    return parse_envelope(body, agent=agent, round_no=round_no)


def state():
    return DebateState(["claude", "gpt"], max_debate=3, stall_limit=3)


DONE = '{"message": "ship it", "verdict": "DONE"}'


def test_one_agent_alone_can_never_finish():
    s = state()
    s.ingest(env("claude", 1, DONE), "aaa")
    assert not s.consensus_reached("aaa", gate_ok=True)
    assert "gpt has not voted DONE" in " ".join(s.why_not_done("aaa", True))


def test_both_done_on_the_same_state_finishes():
    s = state()
    s.ingest(env("claude", 1, DONE), "aaa")
    s.ingest(env("gpt", 2, DONE), "aaa")
    assert s.consensus_reached("aaa", gate_ok=True)


def test_a_signoff_does_not_survive_a_change_to_the_workspace():
    s = state()
    s.ingest(env("claude", 1, DONE), "aaa")
    s.ingest(env("gpt", 2, DONE), "bbb")  # gpt changed files, then approved
    assert not s.consensus_reached("bbb", gate_ok=True)
    assert "has since changed" in " ".join(s.why_not_done("bbb", True))


def test_a_failing_gate_overrides_both_agents():
    s = state()
    s.ingest(env("claude", 1, DONE), "aaa")
    s.ingest(env("gpt", 2, DONE), "aaa")
    assert not s.consensus_reached("aaa", gate_ok=False)
    assert s.decide("aaa", gate_ok=False).kind == "continue"


def test_an_open_blocker_overrides_both_agents():
    s = state()
    s.ingest(env("gpt", 1, '{"message":"no","verdict":"CONTINUE","issues":[{"id":"x","title":"t","severity":"blocker"}]}'), "aaa")
    s.ingest(env("claude", 2, DONE), "aaa")
    s.last_verdict["gpt"] = "DONE"
    s.signoffs["gpt"] = s.signoffs["claude"].__class__("gpt", 3, "aaa")
    assert not s.consensus_reached("aaa", gate_ok=True)


def test_a_fix_counts_only_when_the_raiser_accepts_it():
    s = state()
    s.ingest(env("gpt", 1, '{"message":"no","verdict":"CONTINUE","issues":[{"id":"x","title":"t","severity":"major"}]}'), "a")
    s.ingest(env("claude", 2, '{"message":"fixed","verdict":"CONTINUE","resolves":["x"]}'), "b")
    assert s.issues["x"].status == "claimed_fixed"   # claimed, not settled
    assert s.blocking_issues()                        # still blocks a finish

    s.ingest(env("gpt", 3, DONE), "b")                # gpt looked and let it go
    assert s.issues["x"].status == "resolved"
    assert not s.blocking_issues()


def test_a_claimed_fix_the_raiser_rejects_reopens():
    s = state()
    s.ingest(env("gpt", 1, '{"message":"no","verdict":"CONTINUE","issues":[{"id":"x","title":"t","severity":"major"}]}'), "a")
    s.ingest(env("claude", 2, '{"message":"fixed","verdict":"CONTINUE","resolves":["x"]}'), "b")
    report = s.ingest(env("gpt", 3, '{"message":"still broken","verdict":"CONTINUE","issues":[{"id":"x","title":"t","severity":"major"}]}'), "b")
    assert "x" in report.reopened
    assert s.issues["x"].status == "open"


def test_an_agent_may_withdraw_its_own_objection():
    s = state()
    s.ingest(env("gpt", 1, '{"message":"no","verdict":"CONTINUE","issues":[{"id":"x","title":"t","severity":"major"}]}'), "a")
    report = s.ingest(env("gpt", 2, '{"message":"my mistake","verdict":"CONTINUE","resolves":["x"]}'), "a")
    assert "x" in report.withdrawn and s.issues["x"].status == "resolved"


def test_a_circular_argument_goes_to_arbitration():
    s = state()
    raise_it = '{"message":"no","verdict":"CONTINUE","issues":[{"id":"x","title":"t","severity":"major"}]}'
    for round_no in range(1, 5):
        s.ingest(env("gpt", round_no, raise_it), "a")
        s.ingest(env("claude", round_no, '{"message":"I disagree","verdict":"CONTINUE"}'), "a")
    decision = s.decide("a", gate_ok=True)
    assert decision.kind == "arbitrate" and decision.issue_id == "x"

    s.record_arbitration("x", "claude", "compromise — narrow the check", 5)
    assert s.issues["x"].status == "arbitrated"
    assert not s.blocking_issues()                    # a ruling settles it
    assert s.decide("a", gate_ok=True).kind != "arbitrate"


def test_talking_without_building_is_detected_as_a_stall():
    s = state()
    for round_no in range(1, 5):
        s.ingest(env("claude", round_no, '{"message":"as I was saying","verdict":"CONTINUE"}'), "same")
        s.ingest(env("gpt", round_no, '{"message":"indeed","verdict":"CONTINUE"}'), "same")
    assert s.decide("same", gate_ok=True).kind == "stalled"


def test_blocked_is_surfaced_not_swallowed():
    s = state()
    s.ingest(env("claude", 1, '{"message":"I need a credential I do not have","verdict":"BLOCKED"}'), "a")
    assert s.decide("a", gate_ok=True, on_blocked="stop").kind == "blocked"
    assert s.decide("a", gate_ok=True, on_blocked="continue").kind == "continue"


def test_state_survives_a_round_trip():
    s = state()
    s.ingest(env("gpt", 1, '{"message":"no","verdict":"CONTINUE","issues":[{"id":"x","title":"t","severity":"major"}]}'), "a")
    s.ingest(env("claude", 2, DONE), "a")
    restored = DebateState.from_dict(s.to_dict())
    assert restored.issues["x"].title == "t"
    assert restored.last_verdict["claude"] == "DONE"
    assert restored.signoffs["claude"].digest == "a"

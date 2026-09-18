"""Any two agents, in either order."""

import pytest

from duet.config import DEFAULT_PAIR, default_agents, parse_pair


def spec(pair):
    return [(a.name, a.backend, a.model) for a in parse_pair(pair)]


def test_the_default_pair_is_two_subscription_logins():
    assert [a.backend for a in default_agents()] == ["claude-code", "codex-cli"]
    assert DEFAULT_PAIR == "claude+codex"


def test_claude_leads_chatgpt_reviews():
    assert spec("claude+codex") == [
        ("claude", "claude-code", ""),
        ("chatgpt", "codex-cli", ""),
    ]


def test_the_order_flips_who_leads():
    """The whole 'vice versa' case is one word in one flag."""
    assert [a.name for a in parse_pair("codex+claude")] == ["chatgpt", "claude"]


def test_chatgpt_can_be_reached_by_key_instead_of_login():
    assert spec("claude+gpt")[1] == ("chatgpt", "openai-api", "")
    assert spec("claude+openai")[1][1] == "openai-api"


@pytest.mark.parametrize("alias,backend", [
    ("claude", "claude-code"), ("cc", "claude-code"), ("claude-code", "claude-code"),
    ("codex", "codex-cli"), ("chatgpt", "codex-cli"), ("codex-cli", "codex-cli"),
    ("gpt", "openai-api"), ("openai", "openai-api"), ("openai-api", "openai-api"),
])
def test_every_alias_resolves(alias, backend):
    assert parse_pair("%s+mock" % alias)[0].backend == backend


def test_two_of_the_same_agent_are_told_apart():
    """Same model twice is a real setup — a second opinion from the same family
    still catches things — but the transcript has to name them distinctly."""
    names = [a.name for a in parse_pair("claude+claude")]
    assert names == ["claude-1", "claude-2"]
    assert len(set(names)) == 2


def test_models_can_be_pinned_per_side():
    assert spec("claude:opus+claude:sonnet") == [
        ("claude-opus", "claude-code", "opus"),
        ("claude-sonnet", "claude-code", "sonnet"),
    ]


def test_a_model_can_be_pinned_on_one_side_only():
    """The name stays short when nothing collides — the model is recorded in the
    agent's description and printed at session start, so round lines stay readable."""
    assert spec("claude:opus+codex") == [
        ("claude", "claude-code", "opus"),
        ("chatgpt", "codex-cli", ""),
    ]


@pytest.mark.parametrize("bad", ["claude", "claude+codex+gpt", "", "claude+nope", "nope+claude"])
def test_a_bad_pair_explains_itself(bad):
    with pytest.raises(ValueError) as exc:
        parse_pair(bad)
    assert "claude" in str(exc.value).lower()      # the message lists what works


def test_the_pair_order_decides_who_moves_first():
    from duet.config import Config

    cfg = Config(agents=parse_pair("codex+claude"))
    assert cfg.order()[0] == "chatgpt"
    cfg = Config(agents=parse_pair("claude+codex"))
    assert cfg.order()[0] == "claude"

"""Backends. Each one makes a real agent look like a turn-taking peer."""

from duet.adapters.base import Adapter, AgentReply, Probe
from duet.adapters.claude_code import ClaudeCodeAdapter
from duet.adapters.codex_cli import CodexCliAdapter
from duet.adapters.mock import MockAdapter
from duet.adapters.openai_api import OpenAIAdapter

REGISTRY = {
    "claude-code": ClaudeCodeAdapter,
    "openai-api": OpenAIAdapter,
    "codex-cli": CodexCliAdapter,
    "mock": MockAdapter,
}


def build(backend: str, **kwargs) -> Adapter:
    try:
        cls = REGISTRY[backend]
    except KeyError:
        raise SystemExit(
            "unknown backend %r (known: %s)" % (backend, ", ".join(sorted(REGISTRY)))
        )
    return cls(**kwargs)


__all__ = ["Adapter", "AgentReply", "Probe", "REGISTRY", "build"]

"""Adapter contracts, verified against stub binaries and a stubbed API.

The real `claude` and `codex` CLIs are not available in CI, so these tests stand
in for them: they assert the exact flags duet passes and the exact shapes it
parses back.
"""

import json
import os
import stat
from pathlib import Path

import pytest

from duet.adapters.claude_code import ClaudeCodeAdapter
from duet.adapters.codex_cli import CodexCliAdapter
from duet.adapters import openai_api
from duet.adapters.openai_api import OpenAIAdapter


def fake_bin(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


# -- Claude Code -----------------------------------------------------------
CLAUDE_STUB = r'''
printf '%s\n' "$@" > "$ARGDUMP"
cat <<'JSON'
{"type":"result","subtype":"success","is_error":false,
 "result":"Did the thing.\n```json\n{\"message\":\"did it\",\"verdict\":\"DONE\"}\n```",
 "session_id":"sess-123","total_cost_usd":0.04,"num_turns":3}
JSON
'''


def test_claude_adapter_sends_the_right_flags_and_parses_the_result(tmp_path, monkeypatch):
    dump = tmp_path / "args.txt"
    monkeypatch.setenv("ARGDUMP", str(dump))
    binary = fake_bin(tmp_path, "claude", CLAUDE_STUB)

    agent = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path), config={"extra_args": []})
    agent.bin = binary
    reply = agent.send("do the work", system="you are claude")

    assert reply.ok
    assert "did it" in reply.text
    assert reply.meta["session_id"] == "sess-123"
    assert reply.meta["cost_usd"] == 0.04
    assert agent.session_id == "sess-123"

    args = dump.read_text().splitlines()
    assert "-p" in args and "do the work" in args
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert args[args.index("--append-system-prompt") + 1] == "you are claude"
    assert "--resume" not in args          # nothing to resume on the first turn
    assert "--dangerously-skip-permissions" not in args


def test_claude_adapter_resumes_its_session_on_later_turns(tmp_path, monkeypatch):
    dump = tmp_path / "args.txt"
    monkeypatch.setenv("ARGDUMP", str(dump))
    agent = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path))
    agent.bin = fake_bin(tmp_path, "claude", CLAUDE_STUB)

    agent.send("turn one")
    agent.send("turn two")
    args = dump.read_text().splitlines()
    assert args[args.index("--resume") + 1] == "sess-123"


def test_claude_adapter_honours_bypass_mode_only_when_asked(tmp_path, monkeypatch):
    dump = tmp_path / "args.txt"
    monkeypatch.setenv("ARGDUMP", str(dump))
    agent = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path), config={"permission_mode": "bypass"})
    agent.bin = fake_bin(tmp_path, "claude", CLAUDE_STUB)
    agent.send("go")
    args = dump.read_text().splitlines()
    assert "--dangerously-skip-permissions" in args and "--permission-mode" not in args


def test_claude_adapter_accepts_a_stream_of_events(tmp_path):
    agent = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path))
    agent.bin = fake_bin(tmp_path, "claude", r'''
cat <<'JSON'
[{"type":"assistant","message":"thinking"},
 {"type":"result","is_error":false,"result":"final answer","session_id":"s9"}]
JSON
''')
    reply = agent.send("go")
    assert reply.ok and reply.text == "final answer" and agent.session_id == "s9"


def test_claude_adapter_falls_back_to_plain_text(tmp_path):
    agent = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path))
    agent.bin = fake_bin(tmp_path, "claude", 'echo "not json at all"')
    reply = agent.send("go")
    assert reply.ok and "not json" in reply.text


def test_claude_adapter_reports_a_failure_instead_of_pretending(tmp_path):
    agent = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path))
    agent.bin = fake_bin(tmp_path, "claude", 'echo "boom" >&2; exit 1')
    reply = agent.send("go")
    assert not reply.ok and "boom" in reply.error


def test_claude_adapter_explains_a_missing_cli(tmp_path):
    agent = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path))
    agent.bin = str(tmp_path / "definitely-not-here")
    reply = agent.send("go")
    assert not reply.ok and "npm install -g @anthropic-ai/claude-code" in reply.error


def test_claude_adapter_names_the_sign_in_when_the_cli_is_not_logged_in(tmp_path):
    """The CLI answers a prompt with a successful payload whose text says it is
    not logged in. That must surface as a sign-in problem, not as an agent turn."""
    agent = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path))
    agent.bin = fake_bin(tmp_path, "claude", r"""
echo '{"type":"result","subtype":"success","is_error":true,"result":"Not logged in · Please run /login","session_id":"s1"}'
""")
    reply = agent.send("go")
    assert not reply.ok
    assert "claude auth login" in reply.error


# -- sign-in probes (no API keys anywhere) ---------------------------------
def test_claude_probe_reads_a_real_login(tmp_path, monkeypatch):
    binary = fake_bin(tmp_path, "claude",
                      """echo '{"loggedIn": true, "authMethod": "claudeai"}'""")
    monkeypatch.setattr("duet.adapters.claude_code.Adapter.which", staticmethod(lambda *a: binary))
    probe = ClaudeCodeAdapter.probe()
    assert probe.ok and "claudeai" in probe.detail


def test_claude_probe_catches_an_installed_but_signed_out_cli(tmp_path, monkeypatch):
    """The bug this replaces: `--version` succeeds on a signed-out CLI, so duet
    reported ready and then failed on the first turn."""
    binary = fake_bin(tmp_path, "claude",
                      """echo '{"loggedIn": false, "authMethod": "none"}'""")
    monkeypatch.setattr("duet.adapters.claude_code.Adapter.which", staticmethod(lambda *a: binary))
    probe = ClaudeCodeAdapter.probe()
    assert not probe.ok
    assert "not signed in" in probe.detail
    assert "claude auth login" in probe.fix
    assert "API key" in probe.fix          # and it says you do not need one


def test_claude_probe_does_not_claim_ready_when_the_state_is_unreadable(tmp_path, monkeypatch):
    binary = fake_bin(tmp_path, "claude", 'echo "who knows"')
    monkeypatch.setattr("duet.adapters.claude_code.Adapter.which", staticmethod(lambda *a: binary))
    probe = ClaudeCodeAdapter.probe()
    assert not probe.ok and "could not be read" in probe.detail


def test_claude_probe_reports_a_missing_cli_with_an_install_command(monkeypatch):
    monkeypatch.setattr("duet.adapters.claude_code.Adapter.which", staticmethod(lambda *a: None))
    probe = ClaudeCodeAdapter.probe()
    assert not probe.ok and "npm install -g @anthropic-ai/claude-code" in probe.fix


def test_codex_probe_reports_a_chatgpt_login(tmp_path, monkeypatch):
    binary = fake_bin(tmp_path, "codex", 'echo "Logged in using ChatGPT"')
    monkeypatch.setattr("duet.adapters.codex_cli.Adapter.which", staticmethod(lambda *a: binary))
    probe = CodexCliAdapter.probe()
    assert probe.ok and "ChatGPT" in probe.detail


def test_codex_probe_catches_a_signed_out_cli(tmp_path, monkeypatch):
    binary = fake_bin(tmp_path, "codex", 'echo "Not logged in"; exit 1')
    monkeypatch.setattr("duet.adapters.codex_cli.Adapter.which", staticmethod(lambda *a: binary))
    probe = CodexCliAdapter.probe()
    assert not probe.ok and "codex login" in probe.fix


def test_codex_adapter_reports_a_signed_out_cli_instead_of_failing_oddly(tmp_path):
    agent = CodexCliAdapter(name="gpt", cwd=str(tmp_path))
    agent.bin = fake_bin(tmp_path, "codex", 'echo "Not logged in. Run codex login." >&2; exit 1')
    reply = agent.send("go")
    assert not reply.ok and "codex login" in reply.error


# -- OpenAI API ------------------------------------------------------------
def test_openai_adapter_refuses_to_run_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    reply = OpenAIAdapter(name="gpt").send("hi")
    assert not reply.ok and "OPENAI_API_KEY" in reply.error


def test_openai_adapter_builds_a_conversation_and_keeps_history(monkeypatch):
    seen = {}

    def fake_request(url, key, payload=None, timeout=300):
        if url.endswith("/models"):
            return {"data": [{"id": "gpt-5"}, {"id": "gpt-4o"}]}
        seen["payload"] = payload
        return {"model": payload["model"], "usage": {"prompt_tokens": 10},
                "choices": [{"message": {"content": "my answer"}}]}

    monkeypatch.setattr(openai_api, "_request", fake_request)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("DUET_OPENAI_MODEL", raising=False)

    agent = OpenAIAdapter(name="gpt")
    assert agent.send("first", system="be a peer").text == "my answer"
    assert seen["payload"]["model"] == "gpt-5"          # newest the key can see
    assert seen["payload"]["messages"][0] == {"role": "system", "content": "be a peer"}

    agent.send("second", system="be a peer")
    roles = [m["role"] for m in seen["payload"]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]   # it remembers the exchange


def test_openai_adapter_surfaces_an_http_error(monkeypatch):
    import urllib.error

    def boom(url, key, payload=None, timeout=300):
        if url.endswith("/models"):
            return {"data": [{"id": "gpt-4o"}]}
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(openai_api, "_request", boom)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bad")
    reply = OpenAIAdapter(name="gpt", config={"max_retries": 1}).send("hi")
    assert not reply.ok and "401" in reply.error


def test_openai_history_is_trimmed_but_keeps_the_opening_and_the_recent_turns(monkeypatch):
    calls = {}

    def fake_request(url, key, payload=None, timeout=300):
        if url.endswith("/models"):
            return {"data": [{"id": "gpt-4o"}]}
        calls["payload"] = payload
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(openai_api, "_request", fake_request)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    agent = OpenAIAdapter(name="gpt")
    for i in range(20):
        agent.send("turn %d" % i)
    messages = calls["payload"]["messages"]
    assert any("earlier turns trimmed" in str(m.get("content")) for m in messages)
    assert messages[-1]["content"] == "turn 19"
    assert len(messages) <= openai_api.MAX_HISTORY_TURNS + 3


def test_openai_state_round_trips(monkeypatch):
    agent = OpenAIAdapter(name="gpt")
    agent.history = [{"role": "user", "content": "a"}]
    restored = OpenAIAdapter(name="gpt")
    restored.restore(agent.state())
    assert restored.history == agent.history


# -- Codex CLI -------------------------------------------------------------
def test_codex_adapter_passes_sandbox_and_resumes(tmp_path, monkeypatch):
    dump = tmp_path / "args.txt"
    monkeypatch.setenv("ARGDUMP", str(dump))
    agent = CodexCliAdapter(name="gpt", cwd=str(tmp_path))
    agent.bin = fake_bin(tmp_path, "codex", 'printf "%s\\n" "$@" > "$ARGDUMP"; echo "codex says hi"')

    reply = agent.send("build it", system="you are gpt")
    assert reply.ok and "codex says hi" in reply.text
    args = dump.read_text().splitlines()
    assert args[0] == "exec" and "--skip-git-repo-check" in args
    assert args[args.index("--sandbox") + 1] == "workspace-write"
    dumped = dump.read_text()          # the prompt spans lines; check the whole dump
    assert "you are gpt" in dumped and "build it" in dumped

    agent.send("next")
    args = dump.read_text().splitlines()
    assert "resume" in args and "--last" in args


def test_codex_adapter_falls_back_when_resume_is_unsupported(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGDUMP", str(tmp_path / "args.txt"))
    agent = CodexCliAdapter(name="gpt", cwd=str(tmp_path))
    agent.bin = fake_bin(tmp_path, "codex", r'''
for a in "$@"; do if [ "$a" = "resume" ]; then echo "unknown subcommand" >&2; exit 2; fi; done
echo "fresh run ok"
''')
    agent.turns = 1                      # pretend a session exists
    reply = agent.send("go")
    assert reply.ok and "fresh run ok" in reply.text


def test_codex_adapter_prefers_the_last_message_file_over_scraped_stdout(tmp_path):
    """Codex prints a live log; the final message is what duet must parse, so it
    asks for it in a file and only falls back to stdout."""
    agent = CodexCliAdapter(name="gpt", cwd=str(tmp_path))
    agent.bin = fake_bin(tmp_path, "codex", r'''
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; fi
  shift
done
echo "thinking... [2m dim ansi noise"
printf '%s' '{"message":"the real envelope","verdict":"DONE"}' > "$out"
''')
    reply = agent.send("go")
    assert reply.ok
    assert reply.text == '{"message":"the real envelope","verdict":"DONE"}'
    assert reply.meta["used_last_message_file"] is True


def test_codex_adapter_disables_colour_so_output_stays_parseable(tmp_path, monkeypatch):
    dump = tmp_path / "args.txt"
    monkeypatch.setenv("ARGDUMP", str(dump))
    agent = CodexCliAdapter(name="gpt", cwd=str(tmp_path))
    agent.bin = fake_bin(tmp_path, "codex", 'printf "%s\\n" "$@" > "$ARGDUMP"; echo hi')
    agent.send("go")
    args = dump.read_text().splitlines()
    assert args[args.index("--color") + 1] == "never"
    assert args[args.index("-C") + 1] == str(tmp_path)
    assert "-o" in args


def test_agent_clis_are_not_given_an_inherited_stdin(tmp_path):
    """A CLI that reads stdin when it is a pipe hangs forever under duet, which
    is never run from a tty. Both adapters must close it.

    This is a regression test for a real hang: `codex exec` appends piped stdin
    to the prompt, so a live session sat idle for eleven minutes.
    """
    reader = r'''
if [ -t 0 ]; then echo "tty"; else
  # would block indefinitely on an inherited pipe that is never closed
  cat > /dev/null
fi
echo done
'''
    codex = CodexCliAdapter(name="gpt", cwd=str(tmp_path))
    codex.bin = fake_bin(tmp_path, "codex", reader)
    codex.timeout = 20
    assert codex.send("go").ok            # returns rather than hanging

    claude = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path))
    claude.bin = fake_bin(tmp_path, "claude", reader)
    claude.timeout = 20
    assert claude.send("go").ok


def test_claude_is_allowed_to_run_the_gate_itself(tmp_path, monkeypatch):
    """Under acceptEdits the agent can write files but not run them, so it had
    to take the harness's word for the test run. Grant exactly the gate."""
    dump = tmp_path / "args.txt"
    monkeypatch.setenv("ARGDUMP", str(dump))
    agent = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path))
    agent.bin = fake_bin(tmp_path, "claude", 'printf "%s\\n" "$@" > "$ARGDUMP"; echo "{}"')
    agent.allow_gate("pytest -q && npm run lint")
    agent.send("go")

    args = dump.read_text()
    assert "Bash(pytest:*)" in args
    assert "Bash(npm:*)" in args
    assert "--dangerously-skip-permissions" not in args   # not a blanket grant


def test_allowing_the_gate_ignores_a_leading_env_assignment(tmp_path):
    agent = ClaudeCodeAdapter(name="claude", cwd=str(tmp_path))
    agent.allow_gate("CI=1 pytest -q")
    assert agent.allowed_tools == ["Bash(pytest:*)"]


def test_an_adapter_without_permissions_ignores_the_gate(tmp_path):
    agent = CodexCliAdapter(name="gpt", cwd=str(tmp_path))
    agent.allow_gate("pytest -q")      # codex has its own sandbox; nothing to do

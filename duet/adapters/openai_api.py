"""ChatGPT over the OpenAI API — stdlib only, no SDK to install.

This side has no tools of its own, so it builds by emitting `patches` in its
envelope, which the harness applies to the workspace. That is what lets the
roles flip: ChatGPT can lead the build and Claude Code can review it.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from duet.adapters.base import Adapter, AgentReply, Probe

DEFAULT_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

# Tried in order against whatever the key can actually see.
MODEL_PREFERENCE = (
    "gpt-5.1",
    "gpt-5",
    "gpt-5-mini",
    "o3",
    "gpt-4.1",
    "gpt-4o",
)

MAX_HISTORY_TURNS = 24


def api_key(config: Optional[Dict[str, Any]] = None) -> str:
    return str((config or {}).get("api_key") or os.environ.get("OPENAI_API_KEY") or "").strip()


def _request(url: str, key: str, payload: Optional[Dict[str, Any]] = None, timeout: int = 300) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Authorization", "Bearer %s" % key)
    req.add_header("Content-Type", "application/json")
    org = os.environ.get("OPENAI_ORG_ID") or os.environ.get("OPENAI_ORGANIZATION")
    if org:
        req.add_header("OpenAI-Organization", org)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_model(key: str, preferred: str = "") -> str:
    """Pick a model this key can actually call, newest first."""
    if preferred:
        return preferred
    env_model = os.environ.get("DUET_OPENAI_MODEL")
    if env_model:
        return env_model
    try:
        listing = _request("%s/models" % DEFAULT_BASE_URL, key, timeout=60)
    except Exception:
        return MODEL_PREFERENCE[-1]
    available = {str(m.get("id")) for m in listing.get("data", []) if isinstance(m, dict)}
    for candidate in MODEL_PREFERENCE:
        if candidate in available:
            return candidate
    for candidate in sorted(available):
        if candidate.startswith(("gpt-", "o3", "o4")) and "audio" not in candidate and "realtime" not in candidate:
            return candidate
    return MODEL_PREFERENCE[-1]


class OpenAIAdapter(Adapter):
    backend = "openai-api"
    display = "ChatGPT"
    edits_workspace = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.key = api_key(self.config)
        self.base_url = str(self.config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = int(self.config.get("timeout", 300))
        self.max_retries = int(self.config.get("max_retries", 4))
        self.history: List[Dict[str, str]] = []

    def _messages(self, prompt: str, system: str) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        history = self.history
        if len(history) > MAX_HISTORY_TURNS:
            # Keep the opening brief and the recent argument; drop the middle.
            history = history[:2] + [
                {
                    "role": "user",
                    "content": "[%d earlier turns trimmed; the prompt below restates the "
                    "current state of the workspace and every open issue]"
                    % (len(self.history) - MAX_HISTORY_TURNS),
                }
            ] + history[-(MAX_HISTORY_TURNS - 2) :]
        messages.extend(history)
        messages.append({"role": "user", "content": prompt})
        return messages

    def send(self, prompt: str, system: str = "", round_no: int = 0) -> AgentReply:
        if not self.key:
            return AgentReply(
                text="",
                error="OPENAI_API_KEY is not set. Run `duet init` or export it, "
                "or switch this side to the codex backend.",
            )
        if not self.model:
            self.model = resolve_model(self.key, str(self.config.get("model") or ""))

        payload = {"model": self.model, "messages": self._messages(prompt, system)}
        payload.update(self.config.get("params") or {})

        last_error = ""
        for attempt in range(self.max_retries):
            try:
                data = _request(
                    "%s/chat/completions" % self.base_url, self.key, payload, timeout=self.timeout
                )
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = exc.read().decode("utf-8", "replace")[:600]
                except Exception:
                    pass
                last_error = "HTTP %s: %s" % (exc.code, body or exc.reason)
                if exc.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    time.sleep(min(2 ** attempt * 2, 30))
                    continue
                if exc.code == 400 and "max_tokens" in body:
                    payload.pop("max_tokens", None)
                    continue
                if exc.code == 401:
                    last_error += "\nThe key was rejected. Check OPENAI_API_KEY."
                return AgentReply(text="", error=last_error)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = "network error: %s" % exc
                if attempt < self.max_retries - 1:
                    time.sleep(min(2 ** attempt * 2, 30))
                    continue
                return AgentReply(text="", error=last_error)

            choices = data.get("choices") or []
            text = ""
            if choices:
                text = str((choices[0].get("message") or {}).get("content") or "")
            if not text.strip():
                last_error = "the model returned an empty message"
                if attempt < self.max_retries - 1:
                    continue
                return AgentReply(text="", error=last_error)

            self.history.append({"role": "user", "content": prompt})
            self.history.append({"role": "assistant", "content": text})
            usage = data.get("usage") or {}
            return AgentReply(
                text=text,
                meta={
                    "backend": self.backend,
                    "model": data.get("model") or self.model,
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"),
                },
            )
        return AgentReply(text="", error=last_error or "no response")

    def state(self) -> Dict[str, Any]:
        return {"session_id": self.session_id, "history": self.history, "model": self.model}

    def restore(self, state: Dict[str, Any]) -> None:
        state = state or {}
        self.session_id = state.get("session_id")
        self.history = list(state.get("history") or [])
        self.model = self.model or str(state.get("model") or "")

    @classmethod
    def probe(cls, config: Optional[Dict[str, Any]] = None) -> Probe:
        key = api_key(config)
        if not key:
            return Probe(
                ok=False,
                detail="OPENAI_API_KEY is not set",
                fix="run `duet init`, or export OPENAI_API_KEY=sk-...  "
                "(get one at https://platform.openai.com/api-keys)",
            )
        try:
            listing = _request("%s/models" % DEFAULT_BASE_URL, key, timeout=60)
        except urllib.error.HTTPError as exc:
            return Probe(
                ok=False,
                detail="the API rejected the key (HTTP %s)" % exc.code,
                fix="check OPENAI_API_KEY and that the project has credit",
            )
        except Exception as exc:
            return Probe(ok=False, detail="could not reach the API: %s" % exc)
        model = resolve_model(key)
        count = len(listing.get("data") or [])
        return Probe(ok=True, detail="key works, %d models visible, will use %s" % (count, model))

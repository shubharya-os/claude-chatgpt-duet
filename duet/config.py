from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_DIRNAME = ".duet"
CONFIG_FILENAME = "config.json"
ENV_FILENAME = ".env"


def config_dir(root: str) -> Path:
    return Path(root).expanduser().resolve() / CONFIG_DIRNAME


def load_env_file(root: str) -> List[str]:
    """Load `.duet/.env` into the process env without clobbering real env vars."""
    path = config_dir(root) / ENV_FILENAME
    loaded: List[str] = []
    if not path.is_file():
        return loaded
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
                loaded.append(key)
    except OSError:
        pass
    return loaded


@dataclass
class AgentSpec:
    name: str
    backend: str
    model: str = ""
    options: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Config:
    task: str = ""
    acceptance: str = ""
    root: str = "."
    agents: List[AgentSpec] = field(default_factory=list)
    start: str = ""
    decider: str = ""
    gate: str = ""
    gate_timeout: int = 900
    max_rounds: int = 12
    max_debate: int = 3
    stall_limit: int = 3
    swap_every: int = 0
    on_blocked: str = "stop"
    commit: bool = False

    @property
    def agent_names(self) -> List[str]:
        return [a.name for a in self.agents]

    def agent(self, name: str) -> AgentSpec:
        for spec in self.agents:
            if spec.name == name:
                return spec
        raise KeyError(name)

    def order(self) -> List[str]:
        names = self.agent_names
        if self.start and self.start in names:
            first = self.start
            return [first] + [n for n in names if n != first]
        return names

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["agents"] = [a.to_dict() for a in self.agents]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        data = dict(data or {})
        agents = [
            AgentSpec(**{k: v for k, v in a.items() if k in AgentSpec.__dataclass_fields__})
            for a in data.pop("agents", [])
        ]
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        cfg = cls(**known)
        cfg.agents = agents or default_agents()
        return cfg


def default_agents(prefer_codex: Optional[bool] = None) -> List[AgentSpec]:
    """Claude Code on one side, ChatGPT on the other.

    Codex gives the ChatGPT side its own tools, so it is preferred when present;
    the plain API backend works everywhere and builds through patches instead.
    """
    if prefer_codex is None:
        from duet.adapters.base import Adapter

        prefer_codex = bool(Adapter.which("codex")) and not os.environ.get("OPENAI_API_KEY")
    return [
        AgentSpec(name="claude", backend="claude-code"),
        AgentSpec(name="gpt", backend="codex-cli" if prefer_codex else "openai-api"),
    ]


def default_config(root: str = ".") -> Config:
    return Config(root=str(Path(root).expanduser().resolve()), agents=default_agents())


def config_path(root: str) -> Path:
    return config_dir(root) / CONFIG_FILENAME


def load_config(root: str = ".") -> Config:
    path = config_path(root)
    if path.is_file():
        try:
            cfg = Config.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise SystemExit("could not read %s: %s" % (path, exc))
        cfg.root = str(Path(root).expanduser().resolve())
        return cfg
    return default_config(root)


def save_config(cfg: Config, root: Optional[str] = None) -> Path:
    root = root or cfg.root
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.to_dict()
    data.pop("task", None)
    data.pop("acceptance", None)
    data.pop("root", None)  # config travels with the repo; the path does not
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path

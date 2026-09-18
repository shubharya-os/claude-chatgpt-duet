from __future__ import annotations

import os
import sys
from typing import Any, Dict


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


COLOR = _supports_color()


def paint(text: str, code: str) -> str:
    return "\033[%sm%s\033[0m" % (code, text) if COLOR else text


def bold(t: str) -> str:
    return paint(t, "1")


def dim(t: str) -> str:
    return paint(t, "2")


def red(t: str) -> str:
    return paint(t, "31")


def green(t: str) -> str:
    return paint(t, "32")


def yellow(t: str) -> str:
    return paint(t, "33")


def blue(t: str) -> str:
    return paint(t, "34")


def magenta(t: str) -> str:
    return paint(t, "35")


def cyan(t: str) -> str:
    return paint(t, "36")


AGENT_COLORS = (cyan, magenta, yellow, blue)


def agent_tag(name: str, agents: list) -> str:
    try:
        idx = agents.index(name)
    except ValueError:
        idx = 0
    return AGENT_COLORS[idx % len(AGENT_COLORS)](bold(name))


def verdict_tag(verdict: str) -> str:
    if verdict == "DONE":
        return green("DONE")
    if verdict == "BLOCKED":
        return red("BLOCKED")
    return yellow("CONTINUE")


def wrap(text: str, width: int = 88, indent: str = "     ") -> str:
    import textwrap

    out = []
    for para in (text or "").strip().splitlines():
        if not para.strip():
            out.append("")
            continue
        out.extend(textwrap.wrap(para, width=width, initial_indent=indent, subsequent_indent=indent) or [indent])
    return "\n".join(out)


def rule(char: str = "─", width: int = 72) -> str:
    return dim(char * width)

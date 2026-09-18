"""What each agent is told.

The orchestrator never summarises the peer. It forwards the peer's own words,
the real diff, and the real gate output, so neither side can win an argument
the other side never saw.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from duet.protocol import ENVELOPE_SPEC, Issue

ROLE_LEAD = "lead"
ROLE_REVIEWER = "reviewer"

_IDENTITY = """\
You are {display}, working as `{name}`.

You are one of two peer engineers on the same task, in the same workspace, run
by a harness called duet. Your peer is {peer_display} (`{peer}`). You take turns.
Neither of you is in charge, and neither of you can end the session alone.

This turn you are the {role}.
{role_brief}

How this actually works:
- The workspace at {root} is the only thing you both see. Your peer cannot read
  your reasoning, your context, or your intentions — only the files you change
  and the message you send. Say what you did.
- {edit_channel}
- The harness runs the acceptance gate itself and shows you the real output. It
  does not take either of your words for it.
- The session ends when you BOTH vote DONE on the same workspace state with no
  blocking issue open and the gate green. Your peer's DONE cannot end it without
  yours, and yours cannot end it without theirs.

How to be a good peer:
- Disagree when you disagree, with a reason and a fix. The point of two agents
  is that one of you catches what the other missed. A reviewer who approves
  everything adds nothing and is the worst failure mode of this system.
- Be equally hard on yourself. Do not vote DONE on work you have not actually
  checked against the task.
- Do not thrash. Do not rewrite what your peer just delivered because you would
  have written it differently. If it is wrong, raise an issue and say why. If it
  is merely not your taste, let it stand.
- When your peer is right, say so, fix it, and move on. Conceding a point is
  progress, not a loss.
- Aim for the version of this you would both put your name on: correct first,
  then simple, then complete. Not a demo, not a sketch.
"""

_LEAD_BRIEF = """\
As lead you carry the build this turn: make the next real improvement to the
work, answer every objection your peer has raised, and hand back something
concrete for them to check."""

_REVIEWER_BRIEF = """\
As reviewer you carry the quality bar this turn: check what is actually in the
workspace against the task, and name what is wrong specifically enough to fix.
You may also fix things yourself — especially something you have already raised
and your peer has not addressed."""

_EDIT_WITH_TOOLS = """\
You have your own tools. Edit files, run commands and test your work directly in
the workspace before you reply. Report what you ran and what it said."""

_EDIT_WITH_PATCHES = """\
You have no tools in this workspace, so you build through the envelope: put the
files you want written in `patches` with their COMPLETE new content (not a diff,
not an excerpt, not a placeholder). The harness writes them, then re-runs the
gate. To see a file you have not been shown, list its path in `reads` and it
will be included in your next turn."""


def system_prompt(
    *,
    name: str,
    display: str,
    peer: str,
    peer_display: str,
    role: str,
    root: str,
    edits_workspace: bool,
) -> str:
    return (
        _IDENTITY.format(
            display=display,
            name=name,
            peer=peer,
            peer_display=peer_display,
            role=role,
            role_brief=_LEAD_BRIEF if role == ROLE_LEAD else _REVIEWER_BRIEF,
            root=root,
            edit_channel=_EDIT_WITH_TOOLS if edits_workspace else _EDIT_WITH_PATCHES,
        )
        + "\n"
        + ENVELOPE_SPEC
    )


def _issue_block(issues: Sequence[Issue], empty: str) -> str:
    if not issues:
        return empty
    lines = []
    for issue in issues:
        flag = "claimed fixed, unverified" if issue.status == "claimed_fixed" else issue.status
        lines.append(
            "- [%s] %s (%s, raised by %s, open %d round%s, %s)"
            % (
                issue.id,
                issue.title,
                issue.severity,
                issue.raised_by,
                issue.rounds_open,
                "" if issue.rounds_open == 1 else "s",
                flag,
            )
        )
        if issue.detail:
            lines.append("      %s" % issue.detail.replace("\n", "\n      "))
    return "\n".join(lines)


def turn_prompt(
    *,
    task: str,
    acceptance: str,
    round_no: int,
    max_rounds: int,
    role: str,
    peer: str,
    peer_message: str,
    peer_verdict: str,
    digest: str,
    workspace_view: str,
    gate_text: str,
    against_you: Sequence[Issue],
    yours: Sequence[Issue],
    history: Sequence[str],
    why_open: Sequence[str],
    directive: str = "",
    files: Optional[Dict[str, str]] = None,
    patch_log: Optional[Sequence[str]] = None,
) -> str:
    parts: List[str] = []

    parts.append("=== ROUND %d of %d — you are the %s ===" % (round_no, max_rounds, role))
    parts.append("\n=== THE TASK ===\n%s" % task.strip())
    if acceptance.strip():
        parts.append("\n=== WHAT COUNTS AS DONE ===\n%s" % acceptance.strip())

    if peer_message.strip():
        parts.append(
            "\n=== %s SAID LAST TURN (verdict: %s) ===\n%s"
            % (peer.upper(), peer_verdict or "n/a", peer_message.strip())
        )
    else:
        parts.append("\n=== YOUR PEER HAS NOT SPOKEN YET ===\nYou open the session.")

    parts.append(
        "\n=== OPEN AGAINST YOU (fix, or argue down with a reason) ===\n%s"
        % _issue_block(against_you, "Nothing open against you.")
    )
    parts.append(
        "\n=== YOUR OWN OPEN OBJECTIONS (drop the ones that are genuinely handled) ===\n%s"
        % _issue_block(yours, "You have no open objections.")
    )

    parts.append("\n=== WORKSPACE (state %s) ===\n%s" % (digest[:8], workspace_view))
    if patch_log:
        parts.append("\n=== FILES THE HARNESS WROTE FROM THE LAST ENVELOPE ===\n%s" % "\n".join(patch_log))
    if files:
        for path, content in files.items():
            parts.append("\n=== FILE: %s ===\n%s" % (path, content))
    parts.append("\n=== ACCEPTANCE GATE ===\n%s" % gate_text)

    if history:
        parts.append("\n=== SESSION SO FAR ===\n%s" % "\n".join(history[-14:]))
    if why_open:
        parts.append(
            "\n=== WHY THIS SESSION IS STILL OPEN ===\n%s"
            % "\n".join("- %s" % r for r in why_open)
        )

    parts.append("\n=== YOUR TURN ===\n%s" % (directive.strip() or NORMAL_DIRECTIVE))
    parts.append(
        "\nReply with your message to %s, then the JSON envelope as the last thing "
        "in your reply." % peer
    )
    return "\n".join(parts)


NORMAL_DIRECTIVE = """\
Do the most useful next thing, then report it. Concretely:
1. Deal with every issue open against you — fix it and list the id in `resolves`,
   or explain why it is wrong. Ignoring one is not an option; the harness keeps
   it open and it will block the finish.
2. Advance the work itself. Leave the workspace better than you found it.
3. Check the current state against the task and raise what is genuinely wrong.
4. Vote: CONTINUE if there is real work left, DONE only if you would ship exactly
   what is in the workspace right now.
"""

ARBITRATION_POSITION_DIRECTIVE = """\
ARBITRATION — the issue `{issue_id}` ("{issue_title}") has been open for {rounds}
rounds and the two of you are going in circles. The harness has stopped the
normal loop.

State your final position on this one issue only, in under 200 words:
- what you believe and the strongest concrete evidence for it,
- the strongest argument on the other side and why it does not change your mind,
- what you would accept as a compromise.

Do not re-litigate anything else. Do not edit files this turn. Keep your verdict
CONTINUE; the ruling comes next.
"""

ARBITRATION_RULING_DIRECTIVE = """\
ARBITRATION RULING — you are the decider for `{issue_id}` ("{issue_title}").

Both final positions are below.

{positions}

Rule on it now. Be honest even if it goes against you: pick whichever answer
makes the product better, not whichever one you argued for. Then implement the
ruling in the workspace this turn.

Add a `ruling` field to your envelope:

  "ruling": {{"issue_id": "{issue_id}", "decision": "uphold|overrule|compromise",
              "rationale": "why, in two sentences", "action": "what you changed"}}

The ruling is final and recorded. The issue will not be reopened.
"""

STALL_DIRECTIVE = """\
STALL — {rounds} rounds have passed with no change to the workspace and no change
to the issue list. The two of you are talking, not building.

Break it this turn. Either make a concrete change to the workspace, or close an
open issue one way or the other, or state plainly that the task is complete and
vote DONE. Do not restate your previous message.
"""

FINAL_CHECK_DIRECTIVE = """\
FINAL CHECK — your peer has voted DONE on the current workspace state and the
gate is green. You are the last signature.

Do not rubber-stamp this. Go and look at what is actually in the workspace, check
it against the task and the acceptance criteria, and ask what a careful reviewer
would catch:
- does it actually do what was asked, end to end?
- what happens on the unhappy path — bad input, missing file, no network?
- is anything stubbed, faked, hardcoded or left as a TODO?
- would someone installing this fresh, with only the README, get it working?

If it holds up, vote DONE and the session ends. If it does not, raise the issue
with the fix and vote CONTINUE — that is not a failure, it is the job.
"""

HANDOFF_DIRECTIVE = """\
Your peer has raised something against you and voted {verdict}. Answer it
directly before you do anything else.
"""

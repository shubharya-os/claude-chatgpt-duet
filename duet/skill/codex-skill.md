---
name: duet
description: Hand a task to ChatGPT and Claude Code together, so they build it and check each other until both sign off. Use when the user asks for a second opinion from another model, a cross-check, "what would Claude say", or wants two agents to work a task jointly. Also use when the user is about to ship something risky and wants an independent reviewer in the loop.
---

The user has asked for duet — either by name (`$duet`), or by asking for a second
opinion, a cross-check, or for two agents to work something jointly. Whatever they
asked for after that is the task.

## If the argument is `running` or `status`

Run `duet status` and report it in one short paragraph: still going or finished, which
round, who it is waiting on, what they are arguing about, whether the gate passes. Do not
start a new session.

## If the argument is `review`

Run `duet review --reviewer claude --gate "<the project's test command>"` to get Claude
Code's opinion on the current diff. Report the findings and say which you agree with.
You may disagree — say so and why.

## Otherwise — hand the work over

The user has been talking to you and should not have to repeat any of it.

**1. Write the handoff note** to `/tmp/duet-context.md`: what the user is trying to
achieve in their words, decisions already made and ruled out, constraints they stated,
what you already tried and what happened, and which files matter. Short and specific —
this goes to two engineers who cannot see this conversation. Omit anything you are unsure
of.

**2. Find the gate** — the project's real test or build command. It is the flag that
matters most; without it "done" is just two models agreeing.

**3. Run it:**

```bash
duet run "$ARGUMENTS" --context-file /tmp/duet-context.md --gate "<the gate>" --pair codex+claude
```

`--pair codex+claude` puts you (ChatGPT) in the lead and Claude Code as reviewer, which
matches the conversation the user is already in. Use `claude+codex` if they would rather
Claude lead.

**4. Report back properly.** Whether both signed off and on what; what actually changed,
read from the diff rather than relayed; the disagreement and how it resolved (see
`duet report`); and your own view. If they agreed on something you think is wrong, say
so — you are the third opinion, not a courier.

## Rules

- Never run this over uncommitted work the user cares about without saying so first —
  both agents edit the workspace. Offer a branch.
- Do not re-run to get a nicer answer. Fix what was raised.
- If a side is not signed in, run `duet doctor` and hand the user the fix it prints.

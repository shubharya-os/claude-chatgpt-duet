---
name: duet
description: Get a second opinion from ChatGPT on work done in Claude Code, or hand a task to both agents to build together until they agree. Use when the user asks for a second opinion, a cross-check, "what would ChatGPT say", a review of the current diff by another model, or wants two agents to work a task jointly. Also use when the user is about to ship something risky and wants an independent reviewer.
---

# duet — a second model in the loop

You are Claude Code. `duet` lets you bring ChatGPT in: either for a one-shot review of
what you just did, or for a full build where the two of you take turns until you both
sign off.

The point is that a single agent grades its own homework. duet puts something in the
loop that is allowed to say no, and gives that objection weight.

## Which mode

**`duet review` — one agent, one call, no loop.** The cheap default. Use this when the
user wants a second opinion on work that already exists.

```bash
duet review --gate "pytest -q"
```

It shows ChatGPT the working-tree diff and the gate output, and prints its findings by
severity. Exits 1 if anything blocking was raised.

**`duet run` — both agents, alternating, until both agree.** Use this when the user
wants the work *built* jointly, not just checked. It is slower and uses both
subscriptions, so prefer `review` unless the user asked for the full loop.

```bash
duet run "the task" --gate "pytest -q"
```

Never ends on one agent's say-so: both must vote DONE on the same workspace state,
with no blocking objection open and the gate passing.

## Rules for you

- **Always pass `--gate`** with the project's real test or build command. It is what
  keeps "done" honest — the harness runs it and it overrides both agents' opinions. A
  session without a gate is two models agreeing by argument alone.
- **Never run `duet run` on uncommitted work the user cares about** without saying so
  first. Both agents edit the workspace directly. Suggest a branch.
- **Report the findings, not the exit code.** The user wants to know what ChatGPT
  objected to and whether it was right — say which findings you agree with and which
  you would push back on. You are allowed to disagree with the reviewer.
- **Do not re-run it to get a better answer.** If the reviewer raised something real,
  fix it. Running again until it approves is the exact failure this tool exists to
  prevent.
- If `duet` is missing or a side is not signed in, say so and give the fix
  (`duet doctor` prints it). Do not fall back to reviewing your own work and calling
  it a second opinion.

## Reading the result

`duet review` prints findings grouped by severity: `blocker`, `major`, `minor`.
`duet run` writes `.duet/sessions/<id>/report.md` (outcome, who signed off on what,
every issue and its fate) and `transcript.md` (everything both sides said). When a
session ends without consensus, the report's "what is left" section is the useful part.

Both take `--json` if you need to parse rather than read.

## Picking the pair

`--pair` decides who is involved and who leads. The left one takes the first turn.

| pair | meaning |
|---|---|
| `claude+codex` | default — Claude Code leads, ChatGPT reviews |
| `codex+claude` | ChatGPT leads, Claude Code reviews |
| `claude+gpt` | ChatGPT via an API key instead of a ChatGPT login |
| `claude:opus+claude:sonnet` | two Claude models against each other |

If the user does not say, use the default.

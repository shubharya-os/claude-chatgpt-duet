---
name: duet
description: Hand a task to Claude Code and ChatGPT together, so they build it and check each other until both sign off. Use when the user asks for a second opinion from another model, a cross-check, "what would ChatGPT say", or wants two agents to work a task jointly. Also use when the user is about to ship something risky and wants an independent reviewer in the loop.
---

# duet — a second model in the loop

You are Claude Code. `duet` lets you bring ChatGPT in: either for a one-shot review of
what you just did, or for a full build where the two of you take turns until you both
sign off.

The point is that a single agent grades its own homework. duet puts something in the
loop that is allowed to say no, and gives that objection weight.

## Running it

```bash
{{DUET}} run "the task" --gate "pytest -q"
```

Both agents take turns in the workspace until they agree. It never ends on one agent's
say-so: both must vote DONE on the same workspace state, with no blocking objection open
and the gate passing.

If the project does not exist yet, use `build` instead of `run`: it sets the gate
before any code exists, so it starts red, and makes the pair agree the acceptance
criteria and write the failing tests before they implement anything.

```bash
{{DUET}} build "a CLI that renames photos by the date in their EXIF"
```

Use `build` only in a directory that is empty or nearly so. In a project that already
has tests, `run --gate` is the right command — its real test command is a better gate
than a starter one.

To have the other model check work that already exists, give it that as the task:

```bash
{{DUET}} run "review the current diff against the task; fix what is actually wrong" \
  --gate "pytest -q" --rounds 4
```

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
  (`{{DUET}} doctor` prints it). Do not fall back to reviewing your own work and calling
  it a second opinion.

## Reading the result

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

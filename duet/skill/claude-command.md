---
description: Hand this to Claude Code and ChatGPT together — they build it and check each other until both sign off
argument-hint: <what you want built> | running | review
allowed-tools: Bash({{DUET}}:*), Bash(duet:*), Bash(git:*), Bash(ls:*), Bash(cat:*), Read, Write
---

The user typed `/duet $ARGUMENTS`.

## If the argument is `running` or `status`

Run `{{DUET}} status` and report it in one short paragraph: is it still going, which round,
who it is waiting on, what they are arguing about, and whether the gate is passing.
Nothing else. Do not start a new session.

## If the argument is `review`

Run `duet review`. Report the findings and say
which ones you agree with. You are allowed to disagree with the reviewer — say so and
why. Do not silently act on a finding you think is wrong.

## Otherwise — hand the work over

The user is mid-conversation with you. They should not have to re-explain any of it, and
they should not lose the thread. So:

**1. Write down what you already know.** Create a scratch file (`/tmp/duet-context.md`)
containing the parts of *this* conversation that the two agents would otherwise have to
rediscover:

- what the user is actually trying to achieve, in their words where possible
- decisions already made and explicitly ruled out, and why
- constraints they stated: style, libraries they will not add, files not to touch
- what you have already tried in this session, and what happened
- the files that matter and what you know about them

Be specific and short. This is a handoff note to two engineers who cannot see your
screen, not a summary for the user. Leave out anything you are unsure of — a confident
wrong note is worse than a missing one.

**2. The gate.** duet finds the project's test command itself and prints what it picked.
Only pass `--gate` when it would pick wrong, or when there is something better than the
obvious one — a subset that runs fast, or a lint step worth including. If it reports
finding none, say so in your reply: "done" is then only the two of them agreeing.

**3. Run it:**

```bash
{{DUET}} run "$ARGUMENTS" --context-file /tmp/duet-context.md
```

If the workspace is empty — no source, no tests, nothing to detect — use `build`
instead. It sets the gate before any code exists, so it starts red, and orders the work:
acceptance criteria first, failing tests second, implementation third.

```bash
{{DUET}} build "$ARGUMENTS" --context-file /tmp/duet-context.md
```

Add `--pair codex+claude` if the user wants ChatGPT to lead. It streams; let it. If it
is very long the user can open another terminal and run `{{DUET}} status`.

**4. Report back like a colleague, not a log.** When it finishes:

- Say whether both agents signed off, and on what.
- Say what actually changed — read the diff yourself, do not just relay their summary.
- Pull out the disagreement. The interesting part of any session is what one of them
  objected to and how it got resolved; that is in `{{DUET}} report`.
- Say what you think. If they agreed on something you believe is wrong, say that
  plainly. Two agents agreeing is not proof, and you are the third opinion.
- If it ended without consensus, the report's "what is left" section is the useful part.

## Rules

- **Never run this on uncommitted work the user cares about without telling them first.**
  Both agents edit the workspace directly. Offer a branch.
- **Do not re-run it to get a nicer answer.** If the reviewer raised something real, fix
  it. Re-rolling until it approves is exactly the failure this tool exists to prevent.
- If `duet` is not installed or a side is not signed in, run `{{DUET}} doctor` and give the
  user the fix it prints. Do not quietly review your own work and call it a second
  opinion.

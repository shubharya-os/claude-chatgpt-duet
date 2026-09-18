# duet

[![ci](https://github.com/shubharya-os/claude-chatgpt-duet/actions/workflows/ci.yml/badge.svg)](https://github.com/shubharya-os/claude-chatgpt-duet/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Stop copy-pasting between ChatGPT and Claude Code.**

You already do this by hand. Ask ChatGPT for a plan. Paste it into Claude Code. Copy
what Claude built. Paste it back to ChatGPT. "Looks good, but you missed the error
case." Paste that into Claude Code. Repeat until you get bored and ship it.

You are the messenger. duet is the messenger — and unlike you, it never gets bored,
never loses track of what was said four messages ago, and never lets the two of them
quietly agree they're finished.

```bash
pip install .
duet login                                  # your Claude and ChatGPT plans, no API keys
duet review --gate "pytest -q"              # ChatGPT reviews what Claude Code just did
duet run "fix the retry logic" --gate "pytest -q"   # both of them, until they agree
```

---

## The two things it does

### `duet review` — a second opinion, one command

The cheap one. It shows the other model your working-tree diff and your test output,
and prints what it objects to. One call, no loop.

```bash
duet review --gate "pytest -q"
```

```
duet review  ChatGPT (Codex) on 14 changed files

  blocker  retry-swallows-cancellation
     The bare `except Exception` in fetch.py:41 catches CancelledError, so a
     cancelled request retries instead of stopping. Catch the specific errors.

  minor    test-only-covers-happy-path
     test_retry.py never asserts the backoff delay actually grows.

1 blocker, 1 minor — exit 1
```

Use it before you push. It costs one call and catches the thing you stopped looking for.

### `duet run` — both of them, until they agree

The full loop. They take turns in your repo: one builds, the other reviews, they argue,
they fix. It ends when **both** vote DONE on the **same state of the workspace**, with
no open objection and your tests passing.

Either one can lead. Either one can say no. Neither can finish alone.

---

## Install

**Requirements:** Python 3.9+, Node, a Claude plan and a ChatGPT plan.

```bash
git clone https://github.com/shubharya-os/claude-chatgpt-duet && cd claude-chatgpt-duet
pip install .
npm install -g @anthropic-ai/claude-code @openai/codex
duet login
```

`duet login` runs each CLI's own browser sign-in. **No API keys** — both sides bill to
the subscription you already pay for. duet never touches a credential; it shells out
and afterwards asks each CLI whether it worked.

```bash
duet doctor
```

```
claude   ✓ signed in (claude.ai)
chatgpt  ✓ Logged in using ChatGPT

ready. try: duet run "your task here"
```

`doctor` asks each CLI for its real login state rather than checking a binary exists —
a tool that runs but is signed out is the most common way this kind of thing wastes
your afternoon.

<details>
<summary>Prefer an API key?</summary>

```bash
duet run "..." --pair claude+gpt
export OPENAI_API_KEY=sk-...
```

That backend has no tools of its own, so it builds by returning complete files which
the harness writes. Works fine — just billed per token instead of covered by a plan.
</details>

---

## Pick your pair

`--pair` decides who's involved and who goes first. The left one leads.

| pair | who does what |
|---|---|
| `claude+codex` *(default)* | Claude Code builds, ChatGPT reviews |
| `codex+claude` | ChatGPT builds, Claude Code reviews |
| `claude+gpt` | ChatGPT via API key instead of a login |
| `claude:opus+claude:sonnet` | two Claude models against each other |
| `codex+codex` | two ChatGPT sessions, fresh context each |

```bash
duet run "refactor the parser" --pair codex+claude --gate "pytest -q"
```

Cross-model is the interesting case: two models fail *differently*, so one catches what
the other's blind spot produced. Same-model pairs still work — a reviewer with no
memory of writing the code is a real reviewer — they just share more blind spots.

---

## Use it from inside Claude Code

```bash
duet skill install
```

That installs a skill, so in any Claude Code session you can just say:

> "get a second opinion on this from ChatGPT"
> "have ChatGPT review the diff before I push"
> "work on this with ChatGPT until you both agree"

Claude Code runs duet, reads the findings, and tells you which ones it thinks are right
— it's allowed to disagree with the reviewer, and it will say so.

---

## What a real session looks like

This is duet writing a feature **inside its own repository** — real Claude Code, real
ChatGPT, gated by the test suite:

```
round 1 claude (lead) thinking...
     gate passed
  claude CONTINUE  state 5e7a9342
     Implemented `duet verify` in duet/cli.py, added tests/test_verify.py
     (11 tests), turned the README command block into a table.

round 2 chatgpt (reviewer) thinking...
  chatgpt DONE  state 5e7a9342
     Ran the gate myself: 87 passed. Checked --help and the empty-workspace
     path (exits 2 with guidance). I would ship this.

round 3 claude (lead) thinking...
     gate passed
  claude DONE  state d35aa23b
     Final check found a sentence of mine that was not true: verify exits 0 on
     a session recorded without a gate, having run nothing, and CI reads exit
     codes not prose. Fixed the docs. My edit moved the digest, so you need to
     re-sign.

round 4 chatgpt (reviewer) thinking...
  chatgpt DONE  state d35aa23b
     Re-reviewed the new state. Caveat matches the actual behavior. 87 passed.

BOTH AGENTS SIGNED OFF  both agents signed off on d35aa23b
```

Round 3 is why this exists. ChatGPT had **already approved**. Claude's final-check turn
then found a false claim in its own README, and fixing it changed the workspace — which
**voided ChatGPT's sign-off** and forced a fourth round. The second signature wasn't a
formality, and it wasn't free.

---

## How "done" is decided

Four conditions. All enforced by the harness. None negotiable by an agent.

1. **Both agents voted DONE.** One DONE is an opinion.
2. **Both votes point at the same workspace.** Every file hashes to a state id
   (`d35aa23b` above). A sign-off records the id it was cast against — touch any file
   afterwards and that sign-off is dropped. You cannot approve a version that no longer
   exists.
3. **No blocking objection is open.** Vote DONE while naming a blocker and your vote is
   downgraded automatically. You cannot approve and object in one breath.
4. **Your tests pass**, run by the harness on the real files. Neither agent is ever
   *asked* whether they passed.

### Objections belong to whoever raised them

```
chatgpt raises  "empty name is accepted"     → open
claude fixes it, lists it in `resolves`      → claimed_fixed   (still blocks)
chatgpt looks, agrees, drops it              → resolved
chatgpt looks, disagrees, re-raises          → open again
```

The fixer never closes its own ticket. This one rule is what stops two models from
politely agreeing their way to a broken result.

### Deadlocks get ruled on

If an objection survives `--max-debate` rounds, the harness stops the loop, takes a
final ≤200-word position from each side, and makes the decider rule and implement it.
The ruling goes in the report and can't be reopened. If they stop making progress at
all, the stall is detected and both are told to break it.

---

## Commands

```bash
duet review          # one-shot second opinion on the current diff
duet run "task"      # the full loop until both sign off
duet verify          # does the last sign-off still hold? re-runs the gate
duet login           # sign both sides in
duet doctor          # real connection check, with the fix for each side
duet skill install   # use duet from inside Claude Code
duet demo            # the whole loop offline — no keys, no network
duet report          # the last session's report (--transcript for everything said)
duet sessions        # every session in this workspace
```

Useful flags for `run`: `--gate` (your tests — the most valuable one), `--pair`,
`--rounds`, `--accept "what done means"`, `--swap N` to trade places, `--commit`,
`--json`.

---

## Cost and safety

- **The agents write to your working directory.** Run it on a branch you can throw away.
  duet refuses to write outside the workspace, or into `.git/` and `.duet/`.
- Claude Code runs under `acceptEdits`, not a blanket bypass, and is granted Bash for
  your gate's own commands and nothing wider.
- **Two agents cost roughly twice one agent, times the rounds.** `--rounds` is the
  throttle; the default of 12 is deliberately modest. `duet review` is one call.
- duet holds no credentials. Each CLI owns its own sign-in.

---

## Does it actually work?

[docs/QA.md](docs/QA.md) is the honest record: what ran live, what's only covered by
stubs, and the **eleven real bugs** the live runs found — including a signed-out CLI
that reported itself ready, and a `codex exec` that hung forever whenever stdin was a
pipe.

Four of those were found by pointing duet's own ChatGPT side at duet's core and asking
for correctness bugs. All four were real. That's the premise working on its author.

114 tests, no network or credentials needed. CI on Python 3.9, 3.11 and 3.13.

```bash
pytest -q
duet demo     # the real orchestrator against scripted peers
```

---

## Limitations

- **Two models can be wrong together.** The double sign-off raises the floor; it isn't
  proof. The gate is what keeps them honest — always pass `--gate`.
- **Turns are sequential.** A 12-round session is 12 agent invocations.
- **Arbitration is a tiebreak, not an oracle.** It ends circular arguments by recording
  a decision. The report always says who ruled and why.

MIT licensed. Not affiliated with Anthropic or OpenAI.

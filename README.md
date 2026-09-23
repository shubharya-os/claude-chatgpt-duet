# duet

[![ci](https://github.com/shubharya-os/claude-chatgpt-duet/actions/workflows/ci.yml/badge.svg)](https://github.com/shubharya-os/claude-chatgpt-duet/actions/workflows/ci.yml)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Stop copy-pasting between ChatGPT and Claude Code.**

<img src="docs/session.svg" alt="A duet session: Claude Code and ChatGPT taking turns until both sign off" width="760">

> **In one sentence:** AI writes most of the code now, and the same AI decides when
> it is finished. duet makes two AIs from two different companies work the same task
> and requires both to approve the identical result before it counts as done.
>
> **Why that matters:** an agent grading its own homework will tell you it passed.
> Two agents that must agree, with your tests run by neither of them, cannot quietly
> settle for "good enough" — one of them has to be convinced.
>
> **Does it work?** It has been used to build four of its own features, and every one
> of those sessions found real defects in it — including a blocker its own author had
> shipped one message after declaring the work finished. Its newest command was
> handed to it for review before shipping: six findings, five of them real, and
> checking the two it could not run itself proved one of its arguments wrong. All of
> it is written down in [docs/QA.md](docs/QA.md), mistakes included.
>
> *duet is a developer tool: it runs in a terminal and drives Claude Code and ChatGPT
> through your existing subscriptions.*

You already do this by hand. Ask ChatGPT for a plan. Paste it into Claude Code. Copy
what Claude built. Paste it back to ChatGPT. "Looks good, but you missed the error
case." Paste that into Claude Code. Repeat until you get bored and ship it.

You are the messenger. duet is the messenger — and unlike you, it never gets bored,
never loses track of what was said four messages ago, and never lets the two of them
quietly agree they're finished.

```bash
curl -fsSL https://raw.githubusercontent.com/shubharya-os/claude-chatgpt-duet/main/install.sh | sh
```

That does the whole thing: installs duet, then runs `duet setup`, which installs
the two agent CLIs if you do not have them, signs you in to both (your Claude and
ChatGPT plans — **no API keys**), and adds `/duet` to Claude Code and Codex.

Piped from `curl` the installer's own stdin *is* the script, so it reconnects to
your terminal to ask the questions setup needs. Where there is no terminal at all
— CI, a container — it installs `/duet` anyway and prints the one command left:

```bash
duet setup
```

Everything skips whatever is already done, so re-running is safe and is how you
update.

**Only have one of them?** duet still runs. `--pair claude:opus+claude:sonnet` puts
two Claude models against each other; `--pair codex+codex` uses two ChatGPT sessions
with fresh context each. Two vendors is the stronger pair — models from different
labs fail differently — but nothing in the mechanism depends on it, and when a side
is missing duet prints the one-subscription command with your own arguments already
in it.

<details>
<summary>or with pip, if you would rather not pipe a script to a shell</summary>

```bash
pip install git+https://github.com/shubharya-os/claude-chatgpt-duet
duet setup
```

On a system where Python is "externally managed" (Homebrew, modern Debian), plain
`pip install` is refused — use `pipx install git+…`, or the installer above, which
falls back to a virtualenv it owns at `~/.duet/venv`.
</details>

Then, in the middle of any Claude Code session:

```
/duet add retry with backoff to the fetch client
```

or in Codex, where it is a skill rather than a slash command:

```
$duet add retry with backoff to the fetch client
```

It takes the conversation you are already in — what you are building, what you ruled
out, what you already tried — hands it to both agents, and they work it until they both
sign off. You never leave the thread, and you never re-explain anything.

```
/duet running     check on a session in progress
/duet review      second opinion on the current diff
```

Starting a project rather than changing one? `duet build "<idea>"` sets the gate
**before any code exists**, so it is red from round one — the pair has to agree what
done means and write the failing tests before they are allowed to build anything.

```bash
duet build "a CLI that renames photos by the date in their EXIF"
```

---

## `/duet` — the part you actually use

`duet skill install` places four files, each in the exact directory its host scans:

| file | what it gives you |
|---|---|
| `~/.claude/commands/duet.md` | `/duet` in Claude Code |
| `~/.claude/skills/duet/SKILL.md` | Claude Code reaching for duet on its own |
| `~/.codex/skills/duet/SKILL.md` | duet in Codex — say `$duet`, or just ask for a second opinion |
| `~/.codex/prompts/duet.md` | `/duet` on Codex versions that read that directory |

Codex has skills rather than slash commands, so there it is `$duet` or plain English
("get a second opinion on this from Claude"). You can confirm Codex picked it up with
`codex debug prompt-input x | grep duet` — that is how the location above was verified,
after `~/.codex/prompts` alone turned out not to be enough.

The handoff is the point. Before it runs anything, your assistant writes down what it
already knows — the goal in your words, decisions already made and ruled out, what it
tried and what happened, which files matter — and passes that to the pair as **context,
not instructions**. The task still wins: nothing in the thread can quietly redefine what
you asked for.

Then it reports back like a colleague. Not "exit code 0" — what changed, what one of
them objected to, how it resolved, and whether *it* agrees. It is allowed to tell you the
two of them were wrong.

---

## What it does

### `duet review` — a second opinion, one command

The cheap one. It shows the other model your working-tree diff, the contents of any new
files git has not seen, and — with `--gate` — your test output, then prints what it
objects to. One call, no loop. The reviewer is held read-only by its backend, not asked
politely: `--disallowedTools Edit Write …` on the Claude side, `--sandbox read-only` on
the ChatGPT side.

```
$ duet review --gate "pytest -q"
duet review  ~/code/loader
  reviewer:  chatgpt  ChatGPT (Codex) (codex-cli), sandbox read-only
  gate:      pytest -q  passed

major (1)
  [clear-expired-mutates-during-iteration] clear_expired raises while deleting entries
      clear_expired() iterates self._entries.items() and deletes from self._entries
      inside that same loop, so Python raises RuntimeError: dictionary changed size
      during iteration. Iterate over a snapshot, e.g. list(self._entries.items()).

1 finding (1 major)
blocking: this change should not ship as it is.
```

That is a real finding from a real run — exit 1, because something blocking was raised.
Use it before you push.

### `duet build` — start from nothing, with a gate from round one

An empty directory is where duet's own mechanism used to lapse: there are no tests, so
nothing is detected, and the header reads `gate: none — they can only agree by
argument`. That is also where people build fastest and check least.

```bash
duet build "a CLI that renames photos by the date in their EXIF"
```

The gate is set **before any code exists**, so it starts red, and a red gate blocks both
sign-offs. The pair has to agree what done means and write the tests first — the order
is in the task, not left to their judgement, and the task names the exact gate command
so they cannot write tests it will not run. If the idea names a language, the gate
follows it; on a machine with no test runner at all, duet writes one into
`.duet/gate_unittest.py`, which needs nothing installed.

**Whatever gate is chosen is run once before round one, and refused if it is already
green.** That check exists because the obvious candidates all pass an empty directory:
`unittest discover` exits 0 with no tests before Python 3.12, and `go test ./...` exits
0 printing `[no test files]` for any package without them. A gate like that is not a
weak check — it is the absence of one, wearing the same clothes, and it is precisely the
false proof the double sign-off exists to rule out. Go and Cargo gates are wrapped in
the assertion duet actually makes: at least one test passed.

**Measured, not asserted.** Given "a URL shortener HTTP service … production quality:
input validation, correct status codes, no crash on any malformed request" in an empty
directory, `claude:opus+claude:sonnet` reached a double sign-off in **32 minutes, 6
rounds, 46 tests**. Checked afterwards against the running server rather than its own
suite: correct status codes on every unhappy path, codes surviving a restart, and 40
parallel identical POSTs producing exactly one `201` and thirty-nine `200`s. It also
passes on Python 3.9, which the previous session's artifact did not.

The same brief given to `claude:sonnet+claude:sonnet` finished in **13m 37s** — and
wrote a spec a third the size, with sequential (enumerable) short codes and no URL
deduplication, both of which its own criteria permit. Neither pair broke its rules.
The double sign-off guarantees the criteria are met; it does not choose the criteria,
and the faster run is the one that stated less. Both runs, and what the gate still
cannot catch, are written up in [docs/QA.md](docs/QA.md).

### `duet fix`, `add`, `refactor`, `plan` — the rule is checked, not asked

Agent workflows are usually prompts: *write a failing test first, don't change
behaviour, only plan.* The agent is told the rule and trusted to follow it, and an agent
grading its own work reports that it did. duet has a harness that runs the gate itself
and a veto over consensus, so each of these states its rule to the pair **and checks it
against what actually happened**. When both agents sign off and the rule isn't met, the
sign-offs are cleared and both are told which rule, and why.

| command | the rule | how it is checked |
|---|---|---|
| `duet fix "<bug>"` | the tests must catch the bug | today's tests are **replayed against the original code**; they must fail there and pass now. Deleting a test that existed is refused |
| `duet add "<feature>"` | something must test the feature | a test was added or changed, **and** the tests fail against the code as it was before |
| `duet refactor "<change>"` | behaviour must not change | every test that existed at the start ends **byte-for-byte unchanged**; refused outright if the suite is red before it starts |
| `duet plan "<goal>"` | plan, don't build | only `PLAN.md` may change, and it must not be empty |

```bash
duet fix "slugify('Hello, World!') returns 'hello,-world!' — punctuation should be stripped"
```

The replay is the part worth explaining. The first version of `fix` asked only whether
the gate had ever gone red. The first live run beat it twice over: the pair wrote the
test and the fix in one turn, so the harness never saw red and vetoed a correct fix —
and one agent then **re-broke working code on purpose** to show it a red gate. The final
state was byte-identical to the one it had rejected. A replay runs the finished tests
against a snapshot of the original code instead, so it accepts that fix on the first
sign-off and rejects the one it should: a "fix" whose tests would have passed on the
buggy code too.

What each cannot prove is written in [`workflows.py`](duet/workflows.py) beside the
check. `fix` cannot tell that the failure on the original code is *this* bug rather
than, say, an import of a helper the fix added; `refactor` cannot see behaviour no test
covers. The tasks tell the reviewer to check exactly those.

### `duet run` — both of them, until they agree

They take turns in your repo: one builds, the other reviews, they argue, they fix. It
ends when **both** vote DONE on the **same state of the workspace**, with no open
objection and your tests passing.

Either one can lead. Either one can say no. Neither can finish alone.

```bash
duet run "add retry with backoff to src/fetch.py, and a test that proves it" \
  --gate "pytest -q"
```

**duet finds your test command itself** and prints what it picked, so the flag people
most often forget is not required. Pass `--gate` to override it, `--no-gate` to run
without. It is the important part: your real test command, run by the harness
after every turn. Neither agent may finish while it fails, and neither is ever *asked*
whether it passed — they are both shown the actual output. Without it, the two of them
can only agree by argument.

---

## Install

**Requirements:** Python 3.9+, Node, a Claude plan and a ChatGPT plan.

```bash
curl -fsSL https://raw.githubusercontent.com/shubharya-os/claude-chatgpt-duet/main/install.sh | sh
```

It is short and does nothing you could not do by hand — read it first if you
like. If you would rather:

```bash
npm install -g @anthropic-ai/claude-code @openai/codex
duet login
duet skill install
```

<details>
<summary>or from a clone</summary>

```bash
git clone https://github.com/shubharya-os/claude-chatgpt-duet && cd claude-chatgpt-duet
pip install .
```

Verified down to Python 3.9 with pip 21.2 and setuptools 58. If `duet` is not on your
PATH afterwards, `python3 -m duet` is always equivalent.
</details>

`duet login` runs each CLI's own browser sign-in. **No API keys** — both sides bill to
the subscription you already pay for. duet never touches a credential; it shells out
and afterwards asks each CLI whether it worked.

```bash
duet doctor
```

```
claude   ✓ signed in (claude.ai)
chatgpt  ✓ Logged in using ChatGPT — Codex usage quota not checked

ready. try: duet run "your task here"
```

`doctor` asks each CLI for its real login state rather than checking a binary exists —
a tool that runs but is signed out is the most common way this kind of thing wastes
your afternoon.

It also says what it has *not* checked. A ChatGPT account can be signed in and out of
Codex usage allowance at the same time, and nothing codex offers reports the remaining
allowance without spending a model call — which would bill your quota to tell you about
your quota, on every `doctor` run. So `doctor` claims only what `codex login status`
proves, and duet writes down the one moment the account itself says the allowance is
gone:

```
chatgpt  ✗ signed in, but out of Codex usage quota until 2026-10-18 23:18
             (the account said so at 2026-10-18 11:02)
         fix: wait for 2026-10-18 23:18, upgrade the plan at
              https://chatgpt.com/explore/plus, or run this pair another way:
              `--pair claude+gpt` uses an OpenAI API key instead, and
              `--pair claude:opus+claude:sonnet` uses two Claude models.
```

The note lives in `~/.duet/codex-quota.json`, it is dropped as soon as the stated reset
passes, and the first ChatGPT turn that succeeds clears it — because a turn that ran is
the only cheap proof the allowance is back.

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
duet review --since main   # ...or on everything this branch adds
duet run "task"      # the full loop until both sign off
duet build "idea"    # start a project from nothing, gated from round one
duet fix "bug"       # no sign-off until the tests catch the bug in the original code
duet add "feature"   # no sign-off until a test fails without the feature
duet refactor "..."  # existing tests may not be edited; refused on a red suite
duet plan "goal"     # argue out PLAN.md; no code may change
duet resume          # carry on a session that died, keeping the argument
duet status          # what is the session doing right now (works mid-run)
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

A session is 20–40 minutes of two subscriptions, and an adapter timeout or a
Ctrl-C used to throw the whole argument away. `duet resume` picks it up from the
round after the last one that finished, with the open objections, both sign-offs,
the arbitration rulings and each agent's own thread with its backend intact:

```bash
duet resume                        # the newest unfinished session
duet resume 20250104-142211-9f3a   # or a named one
duet resume --rounds 20            # a new total budget, counting rounds already used
duet resume --gate "pytest -q"     # a different gate than the one it recorded
```

With no id it takes the newest session that has not agreed yet, saying which
newer ones it stepped over and why. It refuses, and names what to type instead,
when that session already agreed, has no rounds left, saved no state, or does
not exist. The gate is re-run before the first new turn, because the workspace
can have changed while the session was dead.

---

## Cost and safety

- **The agents write to your working directory.** Run it on a branch you can throw away.
  duet refuses to write outside the workspace, or into `.git/` and `.duet/`.
- Claude Code runs under `acceptEdits`, not a blanket bypass, and is granted Bash for
  your gate's own commands and nothing wider.
- **Two agents cost roughly twice one agent, times the rounds.** `--rounds` is the
  throttle; the default of 12 is deliberately modest. `duet review` is a single call.
- duet holds no credentials. Each CLI owns its own sign-in.
- **duet has no server, and no telemetry.** Your code reaches exactly the vendors whose
  CLIs you already run — Anthropic through `claude`, OpenAI through `codex` — and no
  third party is added. [SECURITY.md](SECURITY.md) says precisely what is sent, what
  runs, what is written to disk, and which risks remain.

---

## Does it actually work?

[docs/QA.md](docs/QA.md) is the honest record: what ran live, what's only covered by
stubs, and the **thirty-one real bugs** the live runs found — including a signed-out CLI
that reported itself ready, and a `codex exec` that hung forever whenever stdin was a
pipe.

Four of those were found by pointing duet's own ChatGPT side at duet's core and asking
for correctness bugs. All four were real. That's the premise working on its author.

365 tests, no network or credentials needed. CI on Python 3.9, 3.11 and 3.13.

```bash
pytest -q
duet demo     # the real orchestrator against scripted peers
```

---

## "Why not just…"

**…use one agent and read the diff yourself?** Do, when you have time. duet is for the
changes you would otherwise skim. The reviewer's job is to be awake for the boring parts
you stopped checking around file nine.

**…ask the same model to review its own work?** You can, and it helps a little. But it
reviews with the same blind spot that produced the bug, and it is agreeable about its
own output in a way it is not about someone else's. Every bug in
[docs/QA.md](docs/QA.md) that ChatGPT found in this codebase was in code Claude had
already reviewed and been happy with.

**…just use your CI?** CI tells you a test failed. It does not tell you the test never
covered the case, that the fix papered over the cause, or that the README now claims
something untrue. duet runs your CI *and* has something read the change.

**…copy-paste between the two yourself?** That is exactly what this replaces, and you
already know how it goes: by round three you are summarising instead of pasting, you
drop the objection you did not fully understand, and both sides lose the thread. The
machine does not get bored at round three.

**Isn't this just two models agreeing with each other?** It would be, without the rules.
An objection closes only when the agent who *raised* it drops it. A sign-off dies the
moment the workspace changes. Your gate outranks both of them. Take those away and yes,
you would have two chatbots nodding — which is why they are enforced by the harness and
not by the prompt.

**What does it cost?** Roughly double one agent, times the rounds. `duet review` is a
single call. Both bill to subscriptions you already have, not per-token API keys.

---

## Limitations

- **Two models can be wrong together.** The double sign-off raises the floor; it isn't
  proof. The gate is what keeps them honest — always pass `--gate`.
- **Turns are sequential.** A 12-round session is 12 agent invocations.
- **Arbitration is a tiebreak, not an oracle.** It ends circular arguments by recording
  a decision. The report always says who ruled and why.

MIT licensed. Not affiliated with Anthropic or OpenAI.

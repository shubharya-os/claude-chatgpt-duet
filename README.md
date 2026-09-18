# duet

**Claude Code and ChatGPT, on the same task, in the same folder, until both of them sign off.**

You give one task. Two agents work it as peers: one builds, the other reviews, they
swap, they argue, they fix. A harness in the middle keeps score — who objected to
what, whether the fix actually landed, and whether the tests still pass. The session
ends when **both** agents vote `DONE` on the **same state of the workspace**, with no
open objection and the acceptance gate green.

Either one can lead. Either one can say no. Neither one can finish alone.

```bash
git clone https://github.com/shubharya-os/duet && cd duet
pip install .
duet login           # signs in to Claude and ChatGPT — no API keys
duet demo            # runs the whole loop offline, to show you what it does
duet run "add retry with backoff to src/fetch.py, and a test that proves it" --gate "pytest -q"
```

**No API keys.** Both sides authenticate with the subscription you already pay for —
a Claude plan through `claude auth login`, a ChatGPT plan through `codex login`. Your
credentials go to those two CLIs and their own browser sign-in; duet never sees them,
never stores them, and has nothing to leak.

---

## Why two agents instead of one

A single agent grades its own homework. It writes the code, decides the code is
good, and tells you it is done. The failure mode is not that it is stupid — it is
that nothing in the loop is allowed to tell it no.

duet puts something in the loop that is allowed to tell it no, and gives that
objection real weight:

- an objection stays open until **the agent who raised it** says it is fixed — the
  fixer does not get to close it,
- a `DONE` vote is void the moment the workspace changes underneath it,
- the acceptance gate is run by the harness, on the real files, and outranks both
  agents' opinions,
- when they go in circles, the harness stops them, takes a final position from each,
  and forces a recorded ruling.

Two models also fail differently, which is the whole point. The reviewer catches
what the builder's blind spot produced, because it is a different blind spot.

---

## Install

**Requirements:** Python 3.9+, Node (for the two agent CLIs), and a Claude plan and
a ChatGPT plan.

```bash
git clone https://github.com/shubharya-os/duet && cd duet
pip install .           # or: pipx install .
```

Verified down to Python 3.9 with pip 21.2 and setuptools 58. If `duet` is not on
your PATH afterwards, `python3 -m duet` is always equivalent.

Install the two agent CLIs:

```bash
npm install -g @anthropic-ai/claude-code @openai/codex
```

Then sign both in. One command does both:

```bash
duet login
```

It runs `claude auth login` and `codex login` for you, each of which opens its own
browser sign-in. duet never handles a credential: it shells out, waits, and then asks
each CLI whether it worked.

```bash
duet doctor
```

```
claude  (claude-code)
  ✓ signed in (claudeai) (/opt/homebrew/bin/claude)

gpt  (codex-cli)
  ✓ Logged in using ChatGPT (/opt/homebrew/bin/codex)

ready. try: duet run "your task here"
```

`doctor` asks each CLI for its real login state — `claude auth status` and
`codex login status` — rather than checking that the binary exists. A binary that
runs but is signed out is the single most common way this kind of tool wastes your
time, and it is reported as not ready.

### If you would rather use an API key

The key-based path is still there, it is just not the default:

```bash
duet init --gpt-backend openai-api
export OPENAI_API_KEY=sk-...
```

That backend has no tools of its own, so it builds by returning complete files in
`patches` which the harness writes. It works fine — it is simply billed per token
instead of covered by a plan you already have.

---

## Using it

```bash
duet run "port the CSV loader to streaming so it handles a 2GB file" --gate "pytest -q"
```

The `--gate` is the single most valuable flag. It is a shell command the harness runs
itself after every turn. Neither agent may finish while it fails, and neither agent
is asked whether it passed — the harness ran it and both of them are shown the real
output. Without a gate the two of them can only agree by argument; with one, they
have to agree with something that is actually executing.

```bash
duet run "..." --gate "pytest -q"
duet run "..." --gate "npm test && npm run typecheck"
duet run "..." --gate "cargo test && cargo clippy -- -D warnings"
```

Other flags worth knowing:

| flag | what it does |
|---|---|
| `--gpt-backend openai-api` | use an API key instead of a ChatGPT login |
| `--start gpt` | ChatGPT leads and Claude Code reviews (the default is the reverse) |
| `--swap 2` | the lead and reviewer trade places every 2 rounds |
| `--rounds 20` | round limit (default 12) |
| `--max-debate 3` | rounds an objection may survive before forced arbitration |
| `--decider gpt` | who rules on a deadlock (default: the side with its own tools) |
| `--accept "..."` | spells out what "done" means, beyond the task itself |
| `--commit` | git-commits the result once both sign off |
| `-C path` | run against another directory |
| `--json` | machine-readable event stream on stdout |

And the rest of the commands:

```bash
duet login                 # sign both sides in (or `duet login claude` for one)
duet demo                  # the full loop, scripted peers, no keys, no network
duet doctor                # real login check, with the exact fix per side
duet sessions              # every session in this workspace
duet report                # the last session's report
duet report --transcript   # everything both of them actually said
```

---

## What a session looks like

This is `duet demo` — the real orchestrator, with scripted peers so it runs
anywhere:

```
round 1 claude (lead) thinking...
     fs: created greet.py (51 bytes)
     gate passed
  claude CONTINUE  state cf80217e
     First pass at greet.py.

round 2 gpt (reviewer) thinking...
  gpt CONTINUE  state cf80217e
     This ignores half the task: an empty name returns 'Hello, !' instead of
     being rejected.
     opened: no-empty-name-validation

round 3 claude (lead) thinking...
     fs: wrote greet.py (358 bytes)
     gate passed
  claude CONTINUE  state 809799b2
     Fair catch — empty names now raise, and I added a self-check so the gate
     proves it.
     claimed fixed: no-empty-name-validation

round 4 gpt (reviewer) thinking...
  gpt DONE  state 809799b2
     Verified: whitespace-only input raises and the gate runs the check.
     verified fixed: no-empty-name-validation

round 5 claude (lead) thinking...
  claude DONE  state 809799b2

────────────────────────────────────────────────────────────────
BOTH AGENTS SIGNED OFF  both agents signed off on 809799b2
```

Note round 1: the gate **passed** on the broken version, because the gate was weak.
The reviewer caught it anyway. That is the part a single agent with a green test
suite does not do for you.

---

## How "done" is decided

Four conditions, all enforced by the harness, none of them negotiable by an agent:

1. **Both agents voted `DONE`.** One `DONE` is an opinion, not a result.
2. **Both votes point at the same workspace.** Every file in the workspace hashes to
   a state id (`809799b2` above). A sign-off records the id it was cast against; if
   anyone touches a file afterwards, that sign-off is dropped and that agent has to
   look again. You cannot approve a version that no longer exists.
3. **No blocking objection is open.** An agent that votes `DONE` while naming a
   blocker has its vote downgraded automatically — you cannot approve and object in
   the same breath.
4. **The acceptance gate passes**, run by the harness on the real files.

Anything else — round limit, a backend outage, an agent reporting `BLOCKED` — ends
the session as *not* done, and the report says exactly what was still open.

### Objections are owned by whoever raised them

```
gpt raises   "empty name is accepted"        → open
claude fixes it, lists it in `resolves`      → claimed_fixed   (still blocks)
gpt looks, agrees, does not re-raise         → resolved
gpt looks, disagrees, re-raises              → open again
```

A fixer claiming credit is never enough. This one rule is what stops the two of them
from politely agreeing their way to a broken result.

### Deadlocks get ruled on, not ignored

If an objection survives `--max-debate` rounds, the harness suspends the loop, takes
a final ≤200-word position from each agent on that one issue, and hands both to the
decider, who must rule and implement the ruling. The ruling is recorded in the report
and the issue cannot be reopened. If they stop making progress at all — no file
changes, no new arguments — the harness detects the stall and tells both of them to
break it.

---

## The envelope

Every turn ends with one JSON block. Prose above it is for the peer; the envelope is
what the harness acts on.

```json
{
  "message": "what you did, and your answer to each objection raised against you",
  "verdict": "CONTINUE | DONE | BLOCKED",
  "issues": [{"id": "no-input-validation", "title": "empty name is accepted",
              "severity": "blocker", "detail": "why it is wrong and what would fix it"}],
  "resolves": ["an-issue-id-you-have-now-fixed"],
  "patches": [{"path": "src/app.py", "action": "write", "content": "<complete file>"}],
  "summary": "one line for the changelog",
  "confidence": 0.8
}
```

A reply with no envelope is **never** read as agreement — it becomes `CONTINUE`, and
the harness asks that agent to send the envelope again. Full details in
[docs/PROTOCOL.md](docs/PROTOCOL.md).

---

## What it writes to disk

Everything lands in `.duet/sessions/<id>/` in your workspace:

| file | what it is |
|---|---|
| `report.md` | outcome, who signed off on what, every issue and its fate, rulings, timeline |
| `transcript.md` | the full conversation, both sides, every round |
| `events.jsonl` | structured event stream, one JSON object per line |
| `state.json` | complete session state |

`.duet/sessions/` and `.duet/.env` are added to your `.gitignore` by `duet init`.

---

## Safety and cost

- **The agents write to your working directory.** Run it on a git branch you can
  throw away. duet refuses to write outside the workspace, or into `.git/` and
  `.duet/`, but within the workspace they have real reach.
- Claude Code runs with `acceptEdits`, not full bypass. Change it in
  `.duet/config.json` under the claude agent's `options.permission_mode`.
- **Two agents cost roughly twice one agent, times the number of rounds.** `--rounds`
  is your budget control; the default of 12 is deliberately modest. Start there.
- **duet never touches your credentials.** Each CLI owns its own sign-in and its own
  token storage. If you use the optional API-key backend instead, the key is read
  from the environment or `.duet/.env`, never committed, and never passed to the
  other side.
- **Your subscriptions are what pay for this.** Two agents across a dozen rounds is
  real usage on both plans; `--rounds` is the throttle.

---

## Limitations

Worth knowing before you rely on it:

- **Two models can be wrong together.** The double sign-off raises the floor; it does
  not make agreement proof. The gate is what keeps them honest — use one.
- **Turns are sequential.** A round is one agent's full turn, so a 12-round session is
  12 agent invocations, not 12 parallel ones.
- **Codex session continuity is best-effort.** Every prompt duet builds is
  self-contained — the task, the diff, the open issues, the peer's actual words — so
  a dropped session costs context, not correctness.
- **Arbitration is a tiebreak, not a truth oracle.** It ends circular arguments by
  recording a decision and moving on. The report always says who ruled and why.

---

## Development

```bash
pip install -e . pytest   # editable needs pip >= 21.3 and setuptools >= 61
pytest -q                 # 57 tests, no keys and no network required
duet demo                 # the orchestrator end to end against scripted peers
```

The suite covers the rules that matter: a lone `DONE` cannot finish a session, a
sign-off does not survive a change to the workspace, a failing gate overrides both
agents, a claimed fix the raiser rejects reopens, a circular argument reaches
arbitration, a reply with no envelope is not agreement, and a patch aimed outside the
workspace is refused. The adapters are tested against stub binaries and a stubbed API,
so the exact flags passed to `claude` and `codex` — and the shapes parsed back — are
pinned by tests rather than by hope.

MIT licensed.

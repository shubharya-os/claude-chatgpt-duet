# QA record

What has actually been run, on real machines, against real agents — and what has
not. Anything below marked *live* was executed end to end; anything marked
*stubbed* is covered by tests against a fake binary or a fake API, which is weaker
evidence and is labelled as such.

## Automated suite

70 tests, no network and no credentials required. `pytest -q` from a clean clone.

| area | what is pinned |
|---|---|
| envelope parsing | fenced / bare / prose-only replies, example-before-real, verdict and severity synonyms, garbage confidence, duplicate issue ids |
| the two hard rules | a reply with no envelope is `CONTINUE`; `DONE` beside a blocker is downgraded |
| consensus | a lone `DONE` never finishes; a sign-off dies when the workspace changes; a failing gate overrides both agents; an open blocker overrides both agents |
| issue lifecycle | claimed-fixed is not fixed; the raiser accepting closes it; the raiser re-raising reopens it; an agent may withdraw its own |
| deadlock | a circular argument reaches arbitration and the ruling settles it; a stall is detected |
| workspace | state ids are deterministic and ignore `.duet`; patches apply, delete, no-op; paths escaping the workspace, `.git/` or `.duet/` are refused |
| git manners | rendering a diff does not stage the user's files |
| orchestration | happy path, lone-yes, gate veto, arbitration, missing envelope, patch escape, backend outage, either side leading, peer words reaching the peer verbatim |
| adapters | exact flags passed to `claude` and `codex`, session resume, stream-vs-object output, plain-text fallback, signed-out detection, no inherited stdin, last-message file preferred over scraped stdout |
| plumbing | session files written; piped runs stream instead of block-buffering |
| pairing | every alias resolves; order decides who leads; two of the same agent get distinct names; models pinned per side; a bad pair explains itself |
| the skill | installs, is idempotent, refuses to clobber an edited copy without `--force`, and carries the frontmatter Claude Code needs to see it |

## Live runs

| check | result |
|---|---|
| **ChatGPT side, end to end** *(live)* | A full duet session with the real Codex CLI as one peer, signed in with a ChatGPT account. It built the file, answered the reviewer's objection, used `resolves` correctly, and both sides signed off on the same state. `parse_ok=True` on every turn — a real model complied with the envelope format unprompted. |
| **ChatGPT sign-in** *(live)* | `codex login status` → "Logged in using ChatGPT". No API key present in `~/.codex/auth.json`. |
| **Claude sign-in detection** *(live)* | `claude auth status` correctly reported a signed-out CLI, and `duet doctor` refused to call itself ready. |
| **Install** *(live)* | Fresh clone → `pip install .` → `duet demo` on Python 3.9 / pip 21.2 / setuptools 58, and on 3.12. CI covers 3.9, 3.11 and 3.13. |
| **Claude side, end to end** *(live)* | `claude auth status` → signed in via `claude.ai` subscription. A headless `claude -p` round trip returned the expected answer. |
| **Both agents, end to end, on duet itself** *(live)* | duet built a `duet verify` subcommand inside its own repository, Claude Code leading and ChatGPT reviewing, gated by the full test suite. Four rounds, consensus, exit 0. Both models emitted a valid envelope on every turn (`parse_ok=True` throughout) with no prompting beyond the system prompt. Details below. |

## Bugs the live runs found

These are the reason the live runs were worth doing — none of them could have been
caught by the stubbed suite alone.

1. **A signed-out CLI reported as ready.** The probe ran `claude --version`, which
   succeeds whether or not you are signed in, so `duet doctor` said ready and the
   session then failed on its first turn. Probes now ask `claude auth status` and
   `codex login status` for the real state.
2. **`codex exec` hung forever on an inherited stdin pipe.** It appends piped stdin
   to the prompt, so it waited for an EOF that never came. A live session sat idle
   for eleven minutes on turn one. Both adapters now pass `stdin=DEVNULL`.
3. **Piped runs looked frozen.** Reporter output block-buffered as soon as stdout
   was not a terminal, so nothing appeared until the session ended. stdout is now
   line-buffered and every reporter line flushes.
4. **A nicer diff was staging the user's files.** `diff()` ran `git add -AN` so new
   files would show up, quietly changing the index of a repo duet is a guest in.
   New files are now listed from `git status` instead.
5. **The entry point vanished on old pip.** `pip install -e .` on pip < 21.3
   installed the package with no `duet` command and reported success. `pip install .`
   is now the documented path, a `setup.py` shim covers the legacy case, and CI
   asserts a plain install yields a working command.
6. **`duet demo` left its report where nobody would look.** It runs in a temp
   workspace, so a following `duet report` found nothing. The demo now prints the
   exact command to read its own transcript.

## duet on duet: the full two-agent run

Task: add a `duet verify` subcommand that re-runs the recorded gate and reports
whether the last session's sign-off still holds. Gate: the 76-test suite. Claude
Code led, ChatGPT reviewed, on a `duet/self-improve` branch.

| round | agent | verdict | state | what happened |
|---|---|---|---|---|
| 1 | Claude Code | CONTINUE | `5e7a9342` | implemented the command plus 11 tests and turned the README command block into a table |
| 2 | ChatGPT | DONE | `5e7a9342` | ran the gate itself (87 passed), checked `--help` and the empty-workspace path, weighed two design points, approved |
| 3 | Claude Code | DONE | `d35aa23b` | final check found a false claim **in its own README text** and fixed it — moving the state id and voiding ChatGPT's sign-off |
| 4 | ChatGPT | DONE | `d35aa23b` | re-reviewed the new state and re-signed |

Round 3 is the part worth reading. Claude's own sentence said `verify` "exits 0 only
if the state still matches and the gate passes" — but a session recorded without a
`--gate` has nothing to re-run, so `verify` exits 0 having executed nothing, and CI
reads exit codes rather than prose. It fixed the documentation instead of the exit
code, on the grounds that a skipped gate is `ok=True` everywhere else in duet and
making `verify` alone disagree would let `duet run` approve a state that
`duet verify` calls broken. It then said plainly that its edit had moved the digest
and that its peer needed to re-sign.

Round 4 is the other half: ChatGPT's round 2 sign-off did not survive the change,
and the session could not end until it looked again. That is the double-signoff rule
doing its job on a real run rather than in a test.

**Independently checked afterwards, not taken on trust:** 87 tests pass; `duet verify`
exits `0` on a matching state, `1` on a drifted one, `1` on a failing gate, and `2`
when no session exists. The implementation also handles cases the task never
mentioned — a session directory that saved no state, a single `DONE` not counting as
a sign-off, and two `DONE`s against different states not counting either.

### What that run exposed

**Claude Code could not run the gate.** duet launched it with `--permission-mode
acceptEdits`, which allows file edits but not Bash, so every command it tried came
back needing approval in a non-interactive session. It said so honestly rather than
claiming to have run the tests — but a reviewer that cannot execute anything is
reviewing on hearsay, which is the failure this project exists to remove. duet now
translates the gate into `--allowedTools "Bash(pytest:*)"` for exactly the gate's own
executables, and nothing wider. Leading `VAR=value` assignments are stepped over, and
`&&`-joined gates grant each command.

## duet built `duet review`

A second full two-agent session, in a git worktree so it could not collide with work
happening on `main`. Claude Code led, ChatGPT reviewed, gate was the test suite. Four
rounds, consensus, 29 new tests.

The design call was one the task did not ask for. The agent decided that telling a
reviewer "review only, do not edit" **in the prompt is not a control**, and enforced it
per backend instead — `--disallowedTools Edit Write MultiEdit NotebookEdit` on the
Claude side, `--sandbox read-only` on the ChatGPT side — while still checking the
workspace afterwards, because a backend that cannot enforce returns `""` rather than a
false assurance.

Round 3 it re-read its own work instead of assuming it was finished, and found three
real defects, each fixed with a test that fails without the fix. The first is the kind
of thing only a careful reader catches: `untracked_files()` used `git status
--porcelain`, which escapes non-ASCII paths and wraps them in quotes, so a new file with
an accented name reached the reviewer as a name with no body. It switched to
`--porcelain -z`, which is never quoted and also survives spaces and newlines.

**Checked afterwards, not taken on trust:** the reviewer prompt contains no mention of
consensus, DONE votes, sign-off or a peer (the acceptance criterion). A real run against
a deliberately broken cache module found the planted bug — a dict mutated during
iteration in `clear_expired` — named the line, explained the `RuntimeError` it raises,
proposed two fixes, and exited 1.

### What that run exposed

Both agents failed once mid-session, and the loop survived both — but both were real
defects worth fixing:

1. **An agent started a nested duet session.** Testing its own `duet review` meant
   invoking it, which spawned a second pair of agents; the outer turn then waited on the
   whole nested session and hit the 30-minute adapter timeout, losing the turn. duet now
   marks the environment with `DUET_SESSION` and refuses to start a session from inside
   one, unless `--allow-nested` is passed.
2. **A transient blip was read as a sign-out.** One `codex exec` printed "not logged in"
   in a session whose login was verified fine immediately before and after, and the
   adapter believed it and burned the round. It now confirms with `codex login status`
   before giving up, and treats an unconfirmed report as a retryable error.

## Findings from ChatGPT's review of duet

duet's own ChatGPT side was pointed at `consensus.py`, `orchestrator.py`,
`protocol.py` and `workspace.py` and asked for correctness bugs only. All four of
its findings were real, and all four are now fixed with regression tests. This is
the clearest evidence so far that the premise holds: a second model reads your code
differently and catches what you did not.

1. **A large file edited in place kept its state id.** `digest()` summarised files
   over 2MB as `large:<size>`, so a same-length content change produced an identical
   id — carrying a sign-off, and a cached gate result, across a change neither agent
   had seen. Files are now hashed in chunks, with no size shortcut.
2. **A broken decider could close a blocker.** `arbitrate()` recorded a ruling
   whatever came back, so a backend failure marked an unresolved blocker
   `arbitrated`. A failed arbitration now leaves the issue open and waits another
   debate window before retrying, so an outage neither settles the argument nor
   spins the session.
3. **An unparseable reply could verify a claimed fix.** Verification worked by
   noticing the raiser had not re-raised the issue — but a reply with no envelope
   has no issues in it at all, so silence closed the issue. Exactly the thing the
   protocol promises never to do. Verification now requires a reply that parsed.
4. **Symlinks were invisible to the state id.** `tracked_files()` skipped them, so
   retargeting a symlink changed what the project did without changing the id. They
   are now hashed by their target, and never followed.

## Install paths checked live

| path | result |
|---|---|
| `pip install git+https://github.com/shubharya-os/claude-chatgpt-duet` | works from a bare venv; `duet` on PATH; the skill ships inside the wheel |
| `pip install .` from a clone | works down to pip 21.2 / setuptools 58 / Python 3.9 |
| `pip install -e .` | needs pip ≥ 21.3 and setuptools ≥ 61; a `setup.py` shim covers the legacy path, and CI asserts a plain install yields a working command |
| `python3 -m duet` | always equivalent, no PATH needed |

## Known gaps

- The full two-agent live session is pending the Claude sign-in described above.
- Codex session continuity (`codex exec resume --last`) is best-effort. Every prompt
  duet builds is self-contained, so a dropped session costs context, not correctness.
- Two models can still be wrong together. The double sign-off raises the floor; the
  acceptance gate is what keeps it honest.

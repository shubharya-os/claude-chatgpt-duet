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
7. **A signed-in CLI with no quota left reported as ready.** The sibling of bug 1, and
   found the same way: `codex login status` said "Logged in using ChatGPT", `doctor`
   said ready, and the first ChatGPT turn died on `You've hit your usage limit`. Codex
   exposes no way to read the remaining allowance that does not cost a model call — a
   probe that spent one would bill the user's quota to report on their quota, every
   `doctor` run. So `doctor` stopped claiming it: the line now reads "Codex usage quota
   not checked", and the one moment the account does say the allowance is gone is
   written to `~/.duet/codex-quota.json`, reported until the reset it named passes, and
   forgotten on the next turn that succeeds. `duet login` reads the new `Probe.signed_in`
   rather than `ok`, so an exhausted allowance no longer opens a browser at a sign-in
   that was never broken.

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

## duet built `duet resume` — and the session died proving why it was needed

A third session, in its own worktree. It produced the feature (Claude leading,
ChatGPT reviewing) and then **ran out of ChatGPT quota mid-argument**: the account's
Codex allowance was exhausted, and the session ended with one sign-off instead of two.
`duet resume` was used to continue it — the feature recovering the session that was
building it — which worked, and then hit the same wall.

**So `duet resume` does not carry a cross-vendor consensus.** It has Claude's sign-off
and a separate review by a different Claude model (`--pair claude:sonnet+claude:opus`),
which is a real second opinion but not the cross-vendor one this tool exists for. The
green CI badge should not be read as saying otherwise.

That review found three real defects, all now fixed with regression tests:

1. **A round interrupted mid-turn counted as taken.** `take_turn` bumped the round
   counter before calling the adapter, so a Ctrl-C — the headline reason to resume —
   recorded a round that produced nothing. Resume started one round late, gave the turn
   to the other agent (who then spoke twice in a row), and dropped the directive the
   interrupted agent was owed, which is how a stall nudge silently disappears.
2. **`pending_reads` and `pending_patch_log` were used but never saved**, contradicting
   `restore`'s own docstring. A file an agent asked to see vanished across a resume and
   it had to spend a turn asking again.
3. **A malformed state file raised a traceback** out of a command whose entire contract
   is to refuse politely.

### What went wrong on the human side of this

Recorded because they are the same class of error the tool is built to catch.

- **A usage limit was misdiagnosed as a stdin bug**, and code was changed on that
  basis. duet reported the *first* 800 characters of codex's output, which is always
  the startup banner; the real `ERROR:` line is at the end. The change was reverted and
  the actual defect — reporting the banner instead of the error — was fixed.
- **`git checkout` discarded the agents' work mid-merge.** The `state()`/`restore()`
  methods they added to the codex adapter were thrown away and only came back because
  their own test failed during the conflict.
- **An invocation was shipped three times without being run** — the exact failure this
  project exists to prevent, in the tool's own front door. "The file is in the right
  place" is not "the thing runs", and only the second one is worth reporting.
- **A commit was pushed with a failing test**, after changing a message without
  re-reading the test that pinned its wording. CI caught it; the author should have.
- **duet accused the reviewer of editing the workspace** when the edit was a concurrent
  human commit. duet sees the change, not who made it. The message now says what it
  observed and offers both explanations.

## `/duet` in a real session: four attempts, three bugs

Checking that the files landed in the right directories proved nothing. Running
`/duet review` in an actual headless Claude Code session, against a repo with a
planted bug, failed three times — each for a different reason, and each a bug that
would have hit the first person to install this.

1. **`duet` was not on the spawned session's PATH.** A session inherits neither the
   PATH nor the working directory of whoever installed duet, and duet usually lives in
   a venv. The command said plain `duet`; the assistant looked, did not find it, and
   refused to review the diff itself.
2. **Baking in an absolute path broke the permission rule.** `allowed-tools` still
   named a bare `duet`, which no longer matched `<python> -m duet`, so Bash was denied
   outright. One fix caused the next failure.
3. **The baked-in invocation only worked from one directory.** `<python> -m duet`
   resolves only where duet happens to be importable — from anywhere else it is
   `No module named duet`. Two of the three bugs here were the same mistake: writing a
   command into a file without ever running it.

Candidates are now executed, with `--version`, from a directory that is not the
package's own, and the first that works is written in. A `duet` on PATH is preferred
only after it has run, so a present-but-broken one is not trusted either.

The fourth run worked: `/duet review` started a real session with both agents and a
gate. It is also the clearest evidence the instructions hold — under every one of the
three failures the assistant refused to review the change itself and say that was a
second opinion, labelling its own observations as its own:

> "I stopped there rather than reviewing the diff myself and calling that a second
> opinion; the whole point of duet is that the reviewer isn't me."

It also caught two real defects in the throwaway fixture while blocked — a guard whose
branches were identical, and a test file that never imported the function it tested, so
the gate could not have distinguished working code from broken.

## duet on duet, with two Claude models

The ChatGPT side was out of quota, so this one ran `--pair claude:opus+claude:sonnet`
— a real second opinion, from a different model, just not cross-vendor. It was sent
after a false green in duet's own front door: `duet doctor` reported the ChatGPT side
ready when the account was signed in but out of Codex allowance, so the user was told
they were ready and the first ChatGPT turn of their session then died.

**Both agents raised something worse before touching the bug they were sent for.** duet
had auto-detected `pytest -q` on a machine where pytest lives only in a virtualenv, so
the gate failed every turn — and a failing gate vetoes both agents, which made their
own session unwinnable from the first move. They argued correctly, and reached
arbitration, about a session that had already been decided. Two fixes came out of that:

- Gate detection verifies the command can start — on PATH, or as a project-local
  executable — and falls back to `<this python> -m pytest`, which is where pytest
  usually is. If nothing runs, there is no gate and duet says so.
- `duet run` refuses a gate that cannot start, in a second, naming both ways out.

On the original bug they concluded something better than what was asked for. Nothing
codex offers reports remaining quota without spending a model call, and a probe that
burns the user's allowance to report on their allowance is not a probe. Rather than
invent a parser for a file format one of them could not read — it said so, and refused
— duet now makes a smaller true claim and remembers what the account itself said:

- before any failure: `signed in — Codex usage quota not checked (reading it would
  cost a model call)`
- after the account says it is out: `signed in, but out of Codex usage quota until
  2026-10-18 23:18 (the account said so at 2026-09-19 16:37)`, with the reset time
  parsed from codex's own message, and forgotten the moment a turn succeeds.

Verified live, because the account really was out of quota.

One detail worth keeping: the usage-limit match only counts `ERROR` lines, because
codex echoes the prompt back into its own output and duet prompts are full of words
like "quota" — matching anywhere would let a task description about usage limits
convince duet it had hit one.

## Hardening the first five minutes

Two Claude models, four rounds, consensus — and eight breakages in the install path,
every one found by running it rather than reading it. None had been caught by the test
suite. 241 tests became 278.

**The one that matters most was mine, in the code I wrote to prevent exactly it.**
`duet skill install` printed four green ticks, said "In a new Claude Code session:
/duet ...", and exited 0 — *after* `duet_invocation()` had proved that all three ways
of running duet fail. The fallback returned the most explicit candidate with a comment
about naming a real path, and the caller reported success regardless. On a machine
where none of them resolve, duet would have told you `/duet` was ready when it was not.
That is this project's own failure class, written into the function added to stop it.

It now keeps the files, reports red, explains why a spawned session could not run the
command, gives the two-line fix, and returns 1 so `duet setup` cannot print "done."
over the top.

The others: `install.sh` accepted anything executable, so a duet whose interpreter had
been upgraded out from under it passed the check and failed on use; an older `duet`
earlier on PATH silently shadowed a new install; a half-installed venv was not repaired
on a second run, because pip short-circuits a VCS install as "already satisfied"
without `--upgrade`.

### What the two of them did that is worth recording

- Opus raised an issue **against itself** — that CI could not enforce the POSIX-sh
  claim without `dash` and `shellcheck` installed — and Sonnet fixed it by adding both
  to each CI leg.
- Sonnet could not run python in its sandbox. It said so, hand-traced the changes
  against the new tests, checked `git diff --stat` for deletions rather than believing
  "nothing was weakened", and deferred to the harness's gate result instead of claiming
  to have tested.
- Opus, on its final check, went back to `install.sh` rather than rubber-stamp Sonnet's
  sign-off, and found two more.
- Opus flagged a caveat it could not resolve: neither had ever actually run shellcheck.
  "I read the file against shellcheck's default-severity checks and expect nothing to
  fire — but expect is not ran." It declined to raise it as a blocking issue, since the
  CI step already added was the fix and nothing further was available to either of them.

  That caveat has since been closed by hand: shellcheck installed, run against
  `install.sh`, clean. `dash -n` passes too. Expected and observed now agree, which was
  the whole of the distinction being drawn.

## The API backend, run for the first time

`openai-api` is the fallback for anyone without a ChatGPT subscription, and every
test of it replaced `_request` — so urllib, the URL construction, the Authorization
header and the JSON handling had never actually executed. A documented path nobody
had run.

A local HTTP server now stands in for the API, so the real request path runs against
a real socket, and the shape of what duet sends is asserted from the receiving end —
the half a monkeypatched `_request` cannot check. It also covers the `patches`
builder, which is how an agent with no tools of its own writes files; until now that
was only exercised by the mock adapter, which never goes near a network.

Run live first: a full session, ChatGPT-via-API leading and real Claude Code
reviewing. The API side built `greet.py` entirely through `patches`, and Claude found
two genuine faults in it — `greet(123)` leaking an `AttributeError` about `.strip`,
and `greet(None)` raising "name must not be empty", which it called a wrong diagnosis
rather than a wrong outcome: None is the wrong type, and it only passed because
`not None` is incidentally true. Both sides then signed off on the corrected file.

One trap worth recording, since it is not duet's: the first version of the test took
35 seconds, all of it inside `HTTPServer.server_bind`, which resolves the host's FQDN.
On a machine with slow reverse DNS that blocks for tens of seconds. Binding without
the lookup took it to 1 second. A useful test that is slow is a test people skip.

## Running the published one-liner on the user's own machine

Every clean-room test of the installer passed. Running the published command in the
machine owner's own terminal failed on the first line:

    duet needs Python 3.9 or newer.

On a Mac with Python 3.12 installed. `command -v python3` resolves to a broken x86
binary in `/usr/local/bin`, the working interpreter is in `/opt/homebrew/bin`, and
that is not on this user's PATH at all — so searching PATH found nothing usable and
the installer gave up. The same shadowed-toolchain shape that had already broken both
agent CLIs earlier, now in the one command everything else depends on.

It now checks the usual absolute locations after PATH, and every candidate has to
execute before it is accepted, because a binary for the wrong architecture is present,
executable and useless.

Two more from the same round:

- `curl … | sh` left stdin pointing at the script, so `duet setup` had nothing to read
  an answer from and the installer deferred it — making the headline one-liner two
  steps in practice. It reconnects to `/dev/tty` when a terminal exists, and still
  defers cleanly where there is no controlling terminal, such as CI.
- `[ -r /dev/tty ]` was not a sufficient guard: the file can exist and still fail to
  open, and the shell prints "Device not configured" itself. The attempt is made in a
  subshell where that noise can be discarded, and only repeated for real once it is
  known to work. An alarming error in an installer costs trust even when it is
  harmless.

The lesson is the one this project keeps relearning: a clean room is not a machine.
Four of the bugs in this document came from someone else's PATH.

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

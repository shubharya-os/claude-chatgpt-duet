# QA record

What has actually been run, on real machines, against real agents — and what has
not. Anything below marked *live* was executed end to end; anything marked
*stubbed* is covered by tests against a fake binary or a fake API, which is weaker
evidence and is labelled as such.

## Automated suite

368 tests, no network and no credentials required. `pytest -q` from a clean clone.
Counts in this file are as-of their section; this header tracks the current suite.

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

## The install path, verified every way it can run

| how it runs | result |
|---|---|
| `curl … \| sh` with a terminal | runs the whole thing inline and ends on "done." — install, CLI check, both sign-ins, `/duet` in place. Verified under a real pty. |
| `curl … \| sh` with no controlling tty | installs duet and `/duet`, then names the two steps that need a human. No hang. Verified across every POSIX shell present. |
| under the author's own broken PATH | finds `/opt/homebrew/bin/python3` after `command -v python3` resolves to an unusable x86 binary |
| from a wiped machine | virtualenv fallback under PEP 668, `duet --version` good, demo reaches consensus |
| second run | skill files duet itself wrote are recognised as stale and rewritten; a file the user edited is refused without `--force` |

The two steps that remain after one command are a global npm install and two browser
sign-ins. Neither can responsibly be removed: the first is a system-wide change worth
asking about, and OAuth requires a person at a browser. That is the floor, not a gap.

## Removing the last install step

Setup had two steps that needed a human: a global `npm install -g` for the two agent
CLIs, and two browser sign-ins. The second cannot be automated — OAuth needs a person
— but the first turned out to be avoidable: `npx -y @anthropic-ai/claude-code` runs
the CLI with nothing installed globally, and the sign-in lives in the agent's own
config directory, so an npx-run CLI sees the same account. Verified by probing both
CLIs through npx with the global binaries hidden.

duet now resolves a launch command rather than a binary path: a real binary when there
is one, `npx -y <package>` when there is not. Probes resolve it the same way, because
a doctor that reports "not found" for a CLI that runs perfectly well through npx is
two answers about one machine.

Three things went wrong while doing it, all caught by the suite:

1. Execution moved to a new attribute while 41 tests still set `.bin`, so those tests
   silently ran the real CLIs over the network — the suite went from 30 seconds to
   4 minutes 40 and 22 tests failed. `.bin` is a property now: assigning to it rewrites
   the argv, so the two cannot drift.
2. "Nothing found" returned the bare name as though it had been located, and a probe
   read that as present. It returns an empty binary now, with the name kept only for
   the error message.
3. Rewriting the setup branch left the old one in place, so `npm install -g` would
   have run twice — and flattened a distinction the agents had written: npm exiting 0
   while the CLI is still missing means it installed somewhere not on PATH, where "run
   it again" is the wrong advice. Their test caught it.

## Reviewing my own changes, twice

The launch and setup code written in one sitting was the least-reviewed in the repo,
and three bugs had already been caught in it by the test suite. Rather than declare it
finished, a different Claude model reviewed it. It found **eight**, one of them a
blocker that made the whole change pointless:

**`duet login` ran `npx auth login`.** It took the adapter's `.bin` and appended the
agent's subcommands; under the npx fallback `.bin` *is* npx, so signing in asked the
npm registry for packages named `auth` and `login`. The headline capability of the
change, broken on exactly the machine the change exists to serve — and pushed, one
message after I had written "the technical work is done".

The same mistake sat in the codex blip re-check (`npx login status` reads as a
sign-out, producing the precise false verdict that re-check was added to prevent), and
the setup rewrite had moved the consent prompt inside `if has_npx:`, so on a machine
with npm and no npx the global install ran unprompted — the one step that changes
anything outside duet's own directory.

**Then the fixes were reviewed too**, on the same reasoning, and that found three more.
The sharpest: the "report npx honestly" fix was *half applied* — `where` was computed
and then used only on the rare broken-runtime branch, while both success paths still
printed the npx path as the install location. The green line almost every healthy
machine sees still carried the exact misreport the fix claimed to remove. Fixing that
took two passes as well, because the first replacement missed one of the two lines.

Also found: the sign-in failure printed `sub[0]` — "auth exited 1", a command that does
not exist — and `_missing_agent_clis` consulted only PATH while the adapters resolve
`DUET_CLAUDE_BIN`, `~/.claude/local/claude` and `~/.local/bin/codex`, so setup could
abort on a machine where duet runs perfectly well.

Eleven findings across the two reviews, all with tests. The pattern is worth naming:
every one of them is the author being confident about code he had just written, and
none were caught by reading it back.

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

## Starting from nothing, run live

`duet build` was checked the only way that means anything: on an empty directory,
with `claude:opus+claude:sonnet`, task "a tiny CLI that takes a list of numbers on
stdin and prints their median". The point under test was the gate, not the median.

From the session's own event log, unedited:

| when | gate | exit |
|---|---|---|
| before round 1, nothing in the directory | ran | **1 — red** |
| after round 1, tests and implementation written | ran | 0 — green |

That is the claim: the gate was red *before any code existed*, which is what stops a
greenfield session being two models agreeing by argument. Opus voted CONTINUE rather
than DONE on its own first pass, for the stated reason that the criteria had not been
ratified by its peer — the step-1 ordering held without being enforced by anything
but the task text.

It ran 6 rounds and ended in consensus on `e981453a`. `duet verify` re-ran the gate
afterwards and the sign-off still held. Two things are worth reading closely.

**A sign-off stopped counting when the workspace moved.** Sonnet signed off at
`7eadb250` in round 4. Opus then changed a file, which made the state `e981453a`, and
sonnet's approval no longer applied to what was in the directory — so round 6 had to
earn it again. That is the whole point of digesting the workspace rather than
counting votes.

**The last signature was not a rubber stamp.** Round 5 is where duet tells the final
signer it is the last signature. Opus used it to find a silent-wrongness bug in
`render()` that both of the earlier reviews had missed, fixed it, and signed off on
the fix rather than on what it had read.

### The run found a defect in duet, which is the reason to run these

The machine's DNS dropped during round 3. `claude -p` prints its own failure to
stdout and sets `is_error`, so the reply arrived as *non-empty text with an error
attached* — and the orchestrator's guard only caught failures whose text was empty.
The banner fell through to `parse_envelope`, which treats prose as CONTINUE. The
transcript recorded:

```
## Round 3 — claude-opus (lead) — CONTINUE
API Error: Can't reach the API server — check your internet or DNS (ENOTFOUND)
```

A turn that never happened, written down as a considered verdict. It spent a round,
skipped the one-failure retry that exists for exactly this, and showed the peer a
banner as though its partner had said it. Fixed: the reply is parsed first, and a
failed reply with no parseable envelope is recorded as a turn error. An answer that
*did* arrive before the backend fell over is still kept. Both directions have tests,
and the first fails against the old code.

A second defect was caught before it shipped. The starter gate for a machine with no
test runner was going to be `python -m unittest discover -q`, which **exits 0 when it
finds no tests at all** on Python 3.11 and older — and duet supports 3.9. A
greenfield session would have been handed a green gate on an empty directory: a false
proof, which is the one thing the double sign-off exists to rule out. Checked on the
oldest supported interpreter:

```
$ cd /tmp/empty && python3.9 -m unittest discover -q
Ran 0 tests in 0.000s
OK                                    # exit 0 — vacuously green

$ <duet's starter gate, same directory, same interpreter>
no tests ran: this gate stays red until tests exist
                                      # exit 1
```

### What the double sign-off did not catch

The CLI they both signed off on crashes on Python 3.9:

```
TypeError: function takes at most 1 keyword argument (3 given)
  File "median.py", line 88, in wide_context
    return localcontext(prec=..., Emax=..., Emin=...)
```

`decimal.localcontext` only accepts those keywords from Python 3.11. The gate ran on
3.12, so it was green every round, and `ACCEPTANCE.md` — 22 criteria, argued over for
six rounds — never names a Python version. Neither agent was wrong against the
criteria they agreed. The artifact still violates one of them ("no traceback ever")
on an interpreter nobody thought to specify.

This is the honest shape of the guarantee. Two models agreeing against a gate tells
you the gate passed on the machine that ran it and that neither could talk the other
into ignoring a defect it had found. It does not tell you the criteria were complete.
Nothing here fixes that, and it is worth saying plainly rather than discovering it in
someone else's repository.

## duet's review of `duet build`

`duet review --reviewer claude-opus` was pointed at the `duet build` change. Six
findings: four major, two minor. Five were real.

| finding | real? | what it was |
|---|---|---|
| starter gate ignores the language | yes | `duet build "a CLI in Rust"` in an empty directory got a Python gate, turnable green only by building the wrong thing |
| `go test ./...` is vacuously green | yes | exits 0 printing `[no test files]`, so a Go project one file old signs off with no tests |
| unittest fallback misses `tests/` | yes | a conventional `tests/` with no `__init__.py` yields zero tests, and the message blamed the pair for not writing any |
| `why_unusable` splits a quoted path | yes | an interpreter path containing a space made duet refuse to start, over a program called `'/opt/some` |
| "no gate" printed when config has one | yes | the line exists to be trusted, and contradicted the header two lines later |
| change implements build, not verify | **no** | it reviewed against a stale task (see below) |

Two of them it could not run itself and said so. Both were checked here rather
than taken on trust, and the checking changed the answer twice:

- **`go test ./...` on an *empty* module exits 1**, not 0 — the reviewer had that
  part wrong. It exits 0 as soon as one package exists, which is the case that
  matters and which arrives in round 1. So the finding stands and the reasoning
  did not.
- **Neither repair the reviewer proposed for `tests/` works.** `discover(d,
  top_level_dir=".")` raises `ImportError: Start directory is not importable` on
  3.9 and 3.12 alike; unittest requires `__init__.py` and no argument changes
  that. The gate imports test files by path instead.

The wrong finding is the instructive one. `duet review` with no task argument
borrows the task from the last session recorded in the workspace, and said only
"from the last recorded session" — so the reviewer measured a `duet build` change
against a `duet verify` brief six commits old and made that its headline. It was
right about the diff and wrong about the job. The borrowed task now names the
session and how old it is, which is what makes that catchable by whoever reads
the output.

## A same-model pair was one agent wearing two names

Found by running `duet build` with `--pair claude:sonnet+claude:sonnet` to time it
against the opus pair. The session header listed **one** agent:

```
duet  20260920-211703-34ad
  claude-sonnet  Claude Code (claude-code, sonnet)
  order:     claude-sonnet goes first, up to 10 rounds
```

`parse_pair` disambiguates two of the same by appending the model — which
distinguishes nothing when both sides asked for the same model. Both were named
`claude-sonnet`, and neither consequence is cosmetic:

- The orchestrator keys its adapters by name, so **one adapter served both turns**.
  The reviewer was the same session that wrote the code, with its context intact —
  the exact opposite of the property duet sells ("a reviewer with no memory of
  writing the code is a real reviewer").
- Sign-offs are a dict keyed by name too, so the pair could hold at most one.
  `len(signoffs) < len(agents)` was permanently true, so **consensus was
  unreachable**: the session would spend its entire round budget and stop.

The failure was at least in the safe direction — it could not manufacture a false
sign-off, only fail to finish — but it silently wasted a full session, and
`codex+codex` is offered in the README's own pair table. Only identical alias *and*
identical model collided; `claude+claude` and `codex+codex` were already fine
because the index was used when no model was given.

Both are now named `claude-sonnet-1` and `claude-sonnet-2`, with a test for every
pair form and one that runs a same-model pair through the orchestrator to
consensus. Both fail against the old code.

Fixing `parse_pair` alone was not enough. `duet init` writes the pair to
`.duet/config.json`, and a config is read straight back into agents without going
through the parser — so anyone who had already run init with a same-model pair
kept the collision, fix or no fix. The repair now runs on load as well.

## `--commit` was committing duet's own logs

Run for the first time, on a git workspace, with scripted agents. It made a commit:

```
cae896f duet: make it work
 .duet/sessions/20260920-214134-11a2/events.jsonl  |   5 +
 .duet/sessions/20260920-214134-11a2/report.md     |  21 ++++
 .duet/sessions/20260920-214134-11a2/state.json    | 116 +++++++++++++++
 .duet/sessions/20260920-214134-11a2/transcript.md |  19 ++++
```

Every file in it is duet's own bookkeeping, and the message says both agents signed
off on the work. `git add -A` staged `.duet` along with everything else — the same
directory the workspace digest deliberately excludes, because it is not the work.
Anyone running `duet run --commit` on a real repository was getting session logs in
their history.

`.duet` is now unstaged before the commit, and a session that changed nothing
outside it makes no commit at all rather than an empty one wearing a sign-off
message. Both cases have tests; both fail against the old code.

## A production-shaped build, timed

The greenfield test above was a toy. This one was chosen to be app-shaped, and the
clock was running:

> a URL shortener HTTP service: POST /shorten takes a JSON body, GET /<code>
> redirects, codes persist across restarts in SQLite, handles concurrent requests
> safely. Production quality: input validation, correct status codes, no crash on
> any malformed request.

`claude:opus+claude:sonnet`, empty directory, gate red before round one.

**32 minutes 14 seconds. 6 rounds. Consensus on `d48cfbb8`. 46 tests.**

```
built in 32m 14s  — 4 files, 1623 lines
  ACCEPTANCE.md      236 lines
  README.md           89 lines
  shortener.py       573 lines
  test_shortener.py  725 lines
```

Checked afterwards, not taken on trust — a different interpreter, and the running
server rather than its own test suite:

| check | result |
|---|---|
| 46 tests on 3.12 (the gate's interpreter) | pass |
| 46 tests on **3.9** | pass |
| `POST /shorten` | 201 + JSON body |
| `GET /<code>` | 302 to the original |
| malformed JSON body | 400 |
| `javascript:` URL | 400 |
| unknown code | 404 |
| `DELETE /shorten` | 405 |
| restart, then fetch an old code | 302 — persisted |
| **40 parallel identical POSTs** | **exactly one 201, thirty-nine 200** |

The concurrency result is the one worth pausing on: "the same URL posted twice
returns the same code, and exactly one request creates it" was a decision opus made
alone in round 1 and asked its peer to attack. It survived the argument, was encoded
as a criterion, and holds under real parallel load.

### The 3.9 result is the interesting one

The median CLI from the earlier session crashed on Python 3.9, because its criteria
never named a version and the gate ran on 3.12. The task text was changed after that
to require naming the runtime the criteria assume. In this session opus did so in
round 1, unprompted, and said what its own check could not cover:

> A3 pins the floor at CPython 3.9 and enforces it INSIDE the suite with
> `ast.parse(src, feature_version=(3,9))` over our own sources, with the honest
> limit stated: that catches post-3.9 syntax, not post-3.9 stdlib APIs.

Which is exactly the hole the median CLI fell through — `decimal.localcontext` took
keyword arguments only from 3.11, a stdlib API change, not syntax. So the guard the
pair built would not have caught the previous failure. The artifact runs on 3.9
anyway, verified by running it there. One session is not evidence that the task
change works in general; it is evidence that it worked once, and that the pair is now
reasoning about the question at all.

## The same brief, twice: what speed actually costs

The identical brief was given to `claude:opus+claude:sonnet` and to
`claude:sonnet+claude:sonnet`, both from an empty directory.

| | opus + sonnet | sonnet + sonnet |
|---|---|---|
| wall clock | 32m 14s | **13m 37s** |
| rounds | 6 | 4 |
| criteria in ACCEPTANCE.md | 236 lines | 76 lines |
| tests | 46 | 24 |
| implementation | 573 lines | 220 lines |
| tests pass on 3.12 and 3.9 | yes | yes |
| 400 / 404 / 405 / `javascript:` rejected | yes | yes |
| persistence across restart | yes | yes |
| short codes | 7 random characters | **sequential integers** |
| same URL posted 40× in parallel | one `201`, thirty-nine `200` | **forty `201`s** |
| default bind address | `127.0.0.1` | **`0.0.0.0`** |
| configuration | `--host/--port/--db` | environment variables |

Both reached a double sign-off. Both artefacts work, and every unhappy path I threw
at them by hand returned the right status code.

**Neither pair broke its own rules.** The sonnet spec says outright: "Same URL
submitted twice → two *different* codes are acceptable (no deduplication)", and
lists deduplication under non-goals. It was not a corner cut against the criteria —
it is a thinner set of criteria, met exactly.

That is the whole lesson, and it is worth being blunt about: **the double sign-off
guarantees the criteria are met. It does not choose the criteria.** Sequential codes
are enumerable, so anyone can walk 1, 2, 3 and read every URL in the database; a
`0.0.0.0` default puts the service on every interface. Neither is a bug — the first
was decided deliberately, the second was never discussed at all. Nothing in duet
catches an unstated decision, and the run that took less than half as long is the one
that stated less.

So "build it in fifteen minutes" is available, and what it buys you is a smaller
contract. The pair that argued for twice as long argued mostly about step 1.

## Go, run for real

`duet build` picks a Go gate when the idea names Go, and wraps it so zero tests cannot
pass. Until 2026-09-23 that was verified by unit tests and one hand-run shell command.
Then a live session: "a Go CLI that reads a CSV file and prints each column's name,
inferred type, and count of empty cells", `claude:sonnet+claude:sonnet`, empty directory.

**4 minutes 20 seconds, consensus.** The wrapped gate started red. Every promised case
checked by hand afterwards — quoted fields containing commas, type inference, empty
counts, missing file, empty file, ragged rows — matched, including ragged long rows,
whose extra fields `ACCEPTANCE.md` says outright are ignored.

The session never produced the state the wrapper exists for, though — code with no
tests — because the pair wrote `main.go` and `analyze_test.go` in the same turn and the
gate only runs between turns. So it was constructed from the real artefact instead:

```
same code, *_test.go deleted:
  plain go test ./...           exit 0     [no test files]
  duet's wrapped gate           exit 1
```

## Workflows: the first live veto, and what it exposed

`duet fix`, `add`, `refactor` and `plan` each state a rule and check it; see the README.
Two ran live on the first day, both `claude:sonnet+claude:sonnet`.

**`duet refactor`** — "extract the duplicated name and quantity validation in add_item
and remove_item into one helper", on a module with a 21-test suite. **1 minute 46
seconds, 3 rounds, consensus.** The test file's hash afterwards: `be6412659e13d9f7`,
identical to before. A clean helper, all 21 passing. The rule never had to fire.

**`duet fix`** — a planted bug: `slugify("Hello, World!")` returning `hello,-world!`.
The first version of `fix` asked whether the gate had ever gone red. What happened:

```
claude-sonnet-1 DONE  state 8e75764a        test and fix written in one turn
claude-sonnet-2 DONE  state 8e75764a
✗ both signed off, but the `fix` rule is not met
claude-sonnet-1 CONTINUE  state 0b73182d, gate FAILING     re-broke the code on purpose
claude-sonnet-2 CONTINUE  state 8e75764a                    put the fix back
...
BOTH AGENTS SIGNED OFF  both agents signed off on 8e75764a
```

The veto fired — the first time a workflow rule overruled two agents who had both said
done. But it overruled a *correct* fix: the pair wrote a failing test and the fix
together, so the gate only ever saw green. Told why, one agent reintroduced the bug on
purpose so the harness could see it red, and the pair then signed off on
**byte-for-byte the state that had been rejected**. The rule was satisfied by ceremony.
And it was weaker than it looked in the other direction too: a red caused by a typo
would have satisfied it just as well.

So `fix` now replays instead. At sign-off, the harness copies a snapshot of the original
workspace, lays today's test files over it, and runs the gate there. The tests must
fail against the original code and pass on the fixed code. Checked against the real
artefact from this session:

```
replay of the pair's actual fix:       fails on original  →  accepted on first sign-off
replay of a test that misses the bug:  passes on original →  vetoed
```

`add` got the same replay, because "a test file changed" was satisfied by a whitespace
edit. Both replay tests fail with replay disabled — they are the two cases only a replay
can decide.

Then both were run live on the replay code, same pair, fresh bugs:

| | `fix` (history rule) | `fix` (replay) | `add` (replay) |
|---|---|---|---|
| task | punctuation in `slugify` | `parse_price("$1,234.50")` raising | `total_units(stock)` |
| rounds | 6 | **3** | 3 |
| vetoes | 1 — of a correct fix | **0** | 0 |
| turns spent re-breaking working code | 1 | **0** | 0 |
| recorded replay | — | `fails on original` | `fails on original` |

Replayed by hand afterwards rather than trusted: the fixed `parse_price` returns
`1234.5`, and the pair's tests laid over the original code give **1 failed, 2 passed** —
the new test catches the bug and the old ones still hold, which is exactly the
question the replay asks. `total_units` returns 5 and 0 on the obvious cases, 23 tests
pass, 21 of them the originals.


## `duet plan`, and all seven commands run live

"Persist the stock ledger to a JSON file, with writes that cannot leave a half-written
file if the process dies mid-save", on the refactored inventory project. **3 rounds,
consensus.** The rule held without firing: `inventory.py` and `test_inventory.py` came
out byte-identical to how they went in, and `PLAN.md` is 211 lines covering the
atomicity mechanism, order of implementation, what could go wrong at each step, scope
cuts, a section headed *"Where I expect disagreement"*, the reviewer's response, and
verification.

That completes a live run of every session-starting command: `run`, `review`, `build`
(Python and Go), `fix`, `add`, `refactor`, `plan`.

## Replacing ECC, checked before it was removed

ECC was removed from this machine once duet covered its workflow layer. Before removing
it, every local transcript since its install was searched for ECC components that had
actually been invoked. There was one: `ecc:swift-reviewer`, used 4 times across two
sessions in another project. It was kept as a standalone agent, licence notice intact.
Everything else — 386 skills, 67 more agents, 7 hooks, and roughly **41,500 tokens
loaded into every session** — was not in use.

The honest boundary: duet replaces ECC's orchestration workflows (build, fix, add,
refactor, plan, review), and checks their rules where ECC states them. It does not
replace ECC's ~300 domain skills, and does not try to.

## `/duet` from inside Claude Code, with ChatGPT out of quota

The path people actually use is `/duet` in a Claude Code session, and its default pair
needs ChatGPT — whose quota on this machine ran out on 2026-09-19. Driven the way a
user would, three times:

**1. It did not dead-end, but it did not say what it did.** The skill said to run
`doctor` and report the fix. The model inside `/duet` went further: it found the
one-subscription command duet prints and ran it — two Claude models. Then it told the
user the session was running and never mentioned that the "second opinion" was Claude
reviewing Claude. Someone weighing that verdict would take it as cross-vendor. The skill
now requires running the fallback *and* saying so before the result, and a test pins
that in all four skill files.

**2. Plain English routes to the right workflow.** "`/duet initials() crashes with
IndexError when the name has two spaces in a row`" — no command named — started a
`fix` session.

**3. With the fix in place, it disclosed, and said why it matters:**

> Caveat on the verdict, again: this was Opus vs. Sonnet, not Claude vs. ChatGPT. …
> Two Claude models reinforced each other's thoroughness instead of one of them saying
> "this is a toy repo, just make the change." A cross-vendor pair might have pushed
> back on the scope; a same-family pair didn't.

That third run also produced two findings about duet:

- **A 326-line plan for a two-line edit.** The `plan` task listed everything a plan
  should cover and said nothing about size, so a same-family pair gave the toy request
  the full treatment. It now asks for a plan sized to the change, and tells the
  reviewer that thoroughness nobody needed is worth objecting to.
- **Agents in a `plan` session cannot run code.** duet scopes each agent's shell to the
  gate's own commands, and a plan has no gate, so both agents "hit an approval gate
  trying to run Python" and reasoned about behaviour they could have checked. Not
  changed: widening what agents may execute is a change to the safety model, and it
  deserves its own decision rather than a fix in passing. Recorded as a known gap.

## An app, not a tool: timed, then attacked

Everything before this was a single tool — a CLI, a small service. The claim that
matters is apps, so the brief was made app-shaped and deliberately hard:

> a personal expense tracker web app — a stdlib Python server serving a single-page
> HTML/JS UI and a JSON API; add, list, delete, monthly totals per category, CSV export;
> SQLite persistence; validate every input server-side, correct status codes, no crash
> on any malformed request, safe against SQL injection and HTML injection, bind to
> 127.0.0.1 by default.

`claude:opus+claude:sonnet`, empty directory, `duet build`.

**34 minutes 57 seconds. 6 rounds. Consensus.** 22 files, 2,963 lines: `server.py`,
`store.py`, `validation.py`, a static UI, 10 test files, and a 51-criterion
`ACCEPTANCE.md`.

The spec opens with two sections that exist because of earlier sessions in this
document: **§0 "Environment the criteria assume"** (after a CLI crashed on Python 3.9)
and **§1 "Defaults (the things nothing else would check)"** (after a service shipped
on `0.0.0.0` with nobody discussing it). Its first criterion is "binds 127.0.0.1 and
nothing else." It also added a defence the brief never asked for: CSV formula injection.

Then it was attacked — against the running server and in a real browser, not through
its own suite:

| attack | result |
|---|---|
| its own suite on 3.12, and on **3.9** | pass, pass |
| listening address | `127.0.0.1` only |
| `x'); DROP TABLE expenses;--` as a category | stored as text; table intact |
| `=cmd\|/c calc!A1` exported to CSV | written as `'=cmd\|…`, defused |
| not JSON, `NaN`, 3 decimal places, Feb 30, unknown field | 400 each |
| wrong content type, 70 KB body, deleting a missing id, month 13 | 415, 413, 404, 400 |
| `0.1` + `0.2` | total `"0.30"`, exactly |
| 30 simultaneous writes, then a restart | all 36 rows present |
| `<img src=x onerror=alert(1)>` and `<script>` in stored data, **loaded in a browser** | 0 injected elements; both rendered as literal text |
| external resources loaded by the page | none |
| add an expense through the form | row appears, total +7.25 exactly, **no page reload** |
| submit `abc` as the amount | the server's message is shown: *"amount must look like 12.34, with at most 2 decimal places"* |

The last row is recorded because the screenshot said otherwise: it showed an empty,
focused amount field and a stale category, which looked like the submit had not
happened. The DOM said the field held `abc` and the error was on the page. The
screenshot was the thing that was wrong.

What this does not show: it is one app, built once, by one pair. It is not a claim
that every app comes out like this, and nothing here was load-tested beyond thirty
concurrent writes.

## Install paths checked live

| path | result |
|---|---|
| `pip install git+https://github.com/shubharya-os/claude-chatgpt-duet` | works from a bare venv; `duet` on PATH; the skill ships inside the wheel |
| `pip install .` from a clone | works down to pip 21.2 / setuptools 58 / Python 3.9 |
| `pip install -e .` | needs pip ≥ 21.3 and setuptools ≥ 61; a `setup.py` shim covers the legacy path, and CI asserts a plain install yields a working command |
| `python3 -m duet` | always equivalent, no PATH needed |

## Known gaps

- Codex session continuity (`codex exec resume --last`) is best-effort. Every prompt
  duet builds is self-contained, so a dropped session costs context, not correctness.
- Two models can still be wrong together. The double sign-off raises the floor; the
  acceptance gate is what keeps it honest.
- **Agents in a `plan` session cannot execute code.** Their shell is scoped to the
  gate, and a plan has none. See the `/duet` section above.
- **The gate only proves what it runs.** It runs one command, on one machine, with
  one interpreter. The greenfield session above ended in consensus on a CLI that
  crashes on Python 3.9, because the gate ran on 3.12 and the criteria never named a
  version. Agreement between two models is not coverage.

# Changelog

## 0.7.0

- A Claude Code plugin, served from this repository: `/plugin marketplace add
  shubharya-os/duet`, then `/plugin install duet@duet`. About 160 tokens per session.
  Its files are generated from the same sources `duet skill install` writes, and
  `duet skill install` leaves Claude Code alone when the plugin is already there.
- `duet plan --quick`: one full draft, one review that may edit it. About ECC's planning
  time (5 min 59 s against 5 min 23 s, run side by side, tied 4/5 on a blind-graded key
  written first). Reported as *reviewed*, not as signed off by both, unless the reviewer
  changed nothing.
- Refused tool calls are seen by the harness. Claude Code's own `permission_denials`
  are read every turn; a refused edit goes back to the same agent before its peer sees
  the claim, and any other refused call reaches the peer marked unverified. In a live
  plan session that failure cost two rounds.
- `duet plan` agents may run the project's own test command (and nothing else), so a
  claim about what the tests do is checked rather than traced by hand.
- Fixed: the plan rule counted `.pytest_cache/` as a changed file. It now shares the
  workspace's ignore list.
- Fixed: duet could not see a Swift test. Test directories were matched
  case-sensitively, so Xcode's conventional `Tests/` never counted, and no glob knew
  about `*Tests.swift` — an iOS repo looked like a repo with no tests at all, so `add`
  said no test had been added and `fix` had nothing to replay. Three live sessions in
  one ended EXHAUSTED with a green gate and ~90 XCTest cases between them. Test target
  directories (`TwineTests/`, `MyAppUITests/`) count too, and `Contests/` still does
  not. The same pass adds C#, Elixir, Scala, PHP and Dart suffixes.
- Under an `xcodebuild` gate, a test file written during the session is not in the
  original project.pbxproj, so replaying it against the original code would build the
  old target and pass without ever running it. That now reports "could not run" and
  says why, rather than "your tests pass on the original".
- `duet build`'s "is this gate green with nothing here to have passed" check asked a
  second, hand-copied list of test conventions, which had drifted the same way: no
  Swift, and `tests/` only at the top level. An iOS repo whose gate duet detected
  itself — a Makefile running `xcodebuild` — was refused with exit 3 for having no
  tests, next to its ninety XCTest cases. Both questions now go to one detector.
- Fixed: gate detection handed iOS projects `pytest -q`. macOS matches filenames
  without regard to case, so the test for "does this project have Python tests"
  — `(root / "tests").is_dir()` — was also true of Xcode's `Tests/`. The detected
  gate then collected nothing, exited 5 and was red every round, which blocks both
  sign-offs for the whole session. A directory is now read before it is believed;
  an empty `tests/` still means pytest, since that is where a greenfield project is
  about to write them.

## Earlier

- `duet doctor` no longer calls the ChatGPT side ready on the strength of a sign-in.
  An account can be signed in and out of Codex usage allowance at once, and
  `codex login status` only answers the first question, so the line now says what was
  actually checked. Quota is not guessed and never probed with a model call: duet
  records the moment the account itself reports the limit, reports it until the stated
  reset passes, and forgets it on the first turn that succeeds.
- `/duet` in Claude Code and Codex, installed by `duet skill install`, carrying the
  conversation you are already in across to both agents as context.
- `duet resume` — continue an interrupted session instead of starting over, keeping
  the issue ledger, both sign-offs and the round numbering.
- `duet review --since <ref>` — review everything a branch adds, not just uncommitted
  changes, which is what "review my branch" actually means.
- `duet status` — what a session is doing right now, read from the event log so it
  works mid-run.
- `duet review` — a one-shot second opinion on the working-tree diff, with the reviewer
  held read-only by its backend rather than by the prompt.
- `duet verify` — re-run the gate and check the last sign-off still describes this
  workspace.
- `duet resume` — carry on a session that died mid-argument instead of paying for the
  same 20–40 minutes twice. It rebuilds the orchestrator from the state file the dead
  session was already writing after every turn, so the open objections, both sign-offs,
  the arbitration rulings and the round numbering all survive.
- `--pair` — any two agents in either order: `claude+codex`, `codex+claude`,
  `claude+gpt`, `claude:opus+claude:sonnet`.
- Subscription sign-in on both sides. No API keys by default.
- Refuses to start a session from inside one, after an agent testing its own command
  nested a session and lost the turn to a 30-minute timeout.

`duet verify`, `duet review` and `duet resume` were written by duet itself. The bugs
each of those sessions exposed are recorded in [docs/QA.md](docs/QA.md).

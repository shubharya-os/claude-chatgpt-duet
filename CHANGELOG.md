# Changelog

## Unreleased

- `/duet` in Claude Code and Codex, installed by `duet skill install`, carrying the
  conversation you are already in across to both agents as context.
- `duet status` — what a session is doing right now, read from the event log so it
  works mid-run.
- `duet review` — a one-shot second opinion on the working-tree diff, with the reviewer
  held read-only by its backend rather than by the prompt.
- `duet verify` — re-run the gate and check the last sign-off still describes this
  workspace.
- `--pair` — any two agents in either order: `claude+codex`, `codex+claude`,
  `claude+gpt`, `claude:opus+claude:sonnet`.
- Subscription sign-in on both sides. No API keys by default.
- Refuses to start a session from inside one, after an agent testing its own command
  nested a session and lost the turn to a 30-minute timeout.

`duet verify`, `duet review` and `duet resume` were written by duet itself. The bugs
each of those sessions exposed are recorded in [docs/QA.md](docs/QA.md).

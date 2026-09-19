# Contributing

Bug reports and patches welcome. The bar is the same one the tool enforces on its
agents: say what is wrong, and say what would fix it.

## Getting set up

```bash
git clone https://github.com/shubharya-os/claude-chatgpt-duet && cd claude-chatgpt-duet
pip install -e . pytest      # editable needs pip >= 21.3 and setuptools >= 61
pytest -q                    # 278 tests, no network and no credentials needed
duet demo                    # the real orchestrator against scripted peers
```

You do not need a Claude or ChatGPT account to work on most of this. The mock adapter
runs the entire loop offline, and the CLI adapters are tested against stub binaries.

## What a good change looks like

- **A test that fails without it.** Every rule in `consensus.py` is load-bearing; if you
  change one, the test suite should tell the story of what changed.
- **Honest failure modes.** The recurring bug class in this project is something
  reporting success it did not earn — a probe that checks a binary exists rather than
  whether it is signed in, a gate that is skipped but read as passing. If your change
  can report "fine" without having checked, that is the thing to fix.
- **No new dependencies.** duet is stdlib-only on purpose: it installs anywhere, and a
  tool that orchestrates two agents should not be the thing that breaks your venv.

## Running it against the real agents

```bash
npm install -g @anthropic-ai/claude-code @openai/codex
duet login
duet run "your change" --gate "pytest -q"
```

Sessions cost real usage on both subscriptions. `--rounds` is the throttle.

## Reporting a bug

Include what you ran, what you expected, and what happened. If it involves a live
session, `.duet/sessions/<id>/report.md` and `transcript.md` have everything — read them
first and redact anything from your own repo you would not want public.

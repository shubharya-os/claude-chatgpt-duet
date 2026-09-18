# Examples

Every one of these runs in the directory you point it at, writes real files, and
exits `0` only if both agents signed off.

## Build something from nothing

```bash
mkdir ratelimit && cd ratelimit && git init
duet run "Build a token-bucket rate limiter in Python: a RateLimiter class with
  allow(key) -> bool, per-key buckets, configurable rate and burst, thread-safe,
  with pytest tests covering refill over time, burst exhaustion and concurrency." \
  --gate "python -m pytest -q" \
  --accept "Tests pass. No sleeps longer than 0.2s in the suite. Public API is
    documented in a README with a usage example that actually runs."
```

## Fix a bug, with the reviewer holding the line

```bash
duet run "Users report that uploads over 100MB fail silently. Find the cause in
  src/upload.py, fix it, and add a regression test that fails on the old code." \
  --gate "pytest -q tests/test_upload.py"
```

The reviewer's job here is to check that the test actually fails without the fix —
a thing a single agent routinely skips.

## Let ChatGPT lead and Claude Code review

```bash
duet run "Refactor src/parser.py to remove the duplicated tokenizer branches,
  keeping behaviour identical." \
  --start gpt --gate "pytest -q && ruff check ."
```

## Make them trade places

```bash
duet run "Design and implement the caching layer described in docs/rfc-007.md" \
  --swap 2 --rounds 20 --gate "npm test"
```

Neither one stays the critic, so neither one gets comfortable.

## Force a decision on a genuine design disagreement

```bash
duet run "Choose between optimistic locking and a serializable transaction for
  the checkout path, implement the one you choose, and document the trade-off in
  docs/adr/0003-checkout-concurrency.md" \
  --max-debate 2 --decider claude --gate "pytest -q tests/test_checkout.py"
```

With `--max-debate 2` the argument goes to arbitration quickly, and the ruling ends
up in the session report with both final positions.

## Use it from a script

```bash
duet run "$(cat task.md)" --gate "make test" --json --quiet > events.jsonl
echo "exit: $?"   # 0 only on a double sign-off
jq -r 'select(.kind=="turn_done") | "\(.agent) \(.verdict)"' events.jsonl
```

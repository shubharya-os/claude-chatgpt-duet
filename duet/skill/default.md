## duet is the default for real code changes

*If you are already one of the two agents inside a duet session, ignore this
section entirely and do the work yourself — starting another session from inside
one is refused, and only costs you the turn.*

For a real change to code in a project that has tests, hand it to **duet** rather
than working alone: a second model checks the work, and the harness checks the rule
each workflow is held to. Use the `duet` skill, or run it directly:

| the user wants to… | run | the rule the harness checks |
|---|---|---|
| fix a bug | `{{DUET}} fix "<the bug>"` | the tests must fail on the original code |
| add a feature | `{{DUET}} add "<the feature>"` | a test must fail without the feature |
| restructure without changing behaviour | `{{DUET}} refactor "<what>"` | existing tests may not change |
| build something new in an empty directory | `{{DUET}} build "<the idea>"` | the gate is red before any code exists |
| decide how to approach a large change | `{{DUET}} plan "<the goal>"` | only PLAN.md may change |

**Do it directly, without duet,** for: questions and explanations; reading or exploring
code; one-line and mechanical edits (typos, renames, formatting, config values); any
project with no test command and no page to render — a website has a gate,
`{{DUET}} page check index.html`; and whenever the user says to just do it. A duet
session takes minutes and two agents' worth of usage — spend it where a second
opinion and a checked rule can catch something a single pass would not.

**Before starting one, say so in a sentence** — that you are handing it to duet, which
workflow, and why — so the user can say no. When it ends, report which rule it was
held to and what checking it proved, not just that both agents agreed.

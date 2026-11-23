---
name: playtest
description: Plays Tank Tactics for real in debug mode to prove nothing broke. Boots the actual server on a throwaway database and drives it over HTTP, WebSocket and a headless browser, covering lobby, config, every action, death and winning, jury, fog of war, chat, crash recovery, race conditions, malformed input and hundreds of randomized games checked against an independent model of the rules. Use this whenever the user wants to test the game, playtest, check for regressions, asks "did I break anything", "is it safe to ship/deploy/merge", or has just changed game rules, the API, the client or persistence in this repo, even if they only say "test it" or "make sure it still works".
---

# Playtest Tank Tactics

The goal is a trustworthy answer to one question: **is this version safe to ship?** A green result has to mean the game really works, and a red one has to point at a real problem, so the value of this skill lies in reporting honestly what was and wasn't exercised.

The harness is `scripts/playtest.py`. It never imports the game code. It starts the real server on a temporary database and port, so it cannot touch `data/tank-tactics.db` or any deployed game, and it deletes its files when finished.

## Workflow

Run commands from the repo root. On macOS/Linux use `.venv/bin/python`.

**1. See what changed**, so you can look harder there:

```bash
git status --short && git diff --stat main...HEAD
```

**2. Run the project's own tests.** They're fast and pinpoint unit-level breakage:

```bash
.venv/Scripts/python -m pytest -q
```

**3. Run the full playtest**, in the background if your tools allow it (about 2 minutes):

```bash
.venv/Scripts/python .claude/skills/playtest/scripts/playtest.py --out <scratchpad>/playtest.json
```

| Option | Use it for |
|---|---|
| `--quick` | No browser, short fuzz. For a fast check while iterating; don't use it for a ship decision. |
| `--only fog,jury` | Re-run specific areas or scenario names after a fix |
| `--seed N` | Replay the exact random games from a failed run (every run prints its seed) |
| `--fuzz-games N` | More random games. Use 100+ after changing rules in `tank_tactics/game/` |
| `--list` | Show all scenarios |

Exit code 0 means all passed, 1 means failures, 2 means the harness couldn't run. A browser that can't launch makes scenarios **SKIPPED**, which is not a pass: say so in the report, because the browser scenarios are the main coverage of `public/app.js`.

**4. Triage every failure before reporting it.** For each one, decide which it is:

- **Game bug:** the behaviour contradicts `RULES.md`, `NOTES.md` or the invariants in `CLAUDE.md`. Reproduce it with `--only <scenario>` and find the cause in the code.
- **Intentional change:** the user changed this rule on purpose, so the scenario is now out of date. Confirm against their diff and `NOTES.md`, then update the scenario (and the `Model` class, if a rule changed) and mention that you did.
- **Flaky or harness problem:** re-run with the same seed. If it only sometimes fails, report it as flaky rather than dismissing it, since races in this game are real bugs.

Don't weaken a check just to get a green run. A check that's in the way is usually telling you something.

**5. Only report; don't fix** unless the user asked for fixes. They asked whether it broke, and they may want to decide how to handle it.

## Keeping it current

When a feature is added, add a scenario so future runs protect it. Scenarios are short functions registered with `@scenario("area")`; copy a neighbour. If a rule changes, update `Model.apply` as well, deriving it from the rules and not from the new code, because its independence is what lets it catch mistakes.

Google sign-in can't be played end to end here (it needs real Google tokens). The harness only checks that it is safely off without a client ID; `tests/backend/test_auth.py` covers the rest with a fake verifier.

## Report format

Start with the verdict, then keep it short:

```
**Safe to ship** / **Not safe to ship: N problems**

Tested: pytest X passed; playtest Y/Z scenarios, N checks, M random actions (seed S)
Not tested: anything skipped, or "nothing skipped"

Problems (most serious first)
1. What a player would experience. Cause at file:line. How to reproduce.

Outdated scenarios updated: only if any
```

Describe problems by their effect on players ("two players can end up on one tile"), and rank anything that leaks hidden information, loses saved games or crashes the server highest.

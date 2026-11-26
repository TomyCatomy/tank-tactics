---
name: playtest
description: Plays Tank Tactics for real in debug mode to prove nothing broke. Boots the actual server on a throwaway database and drives it over HTTP, WebSocket and a headless browser, covering lobby, config, every action, death and winning, jury, fog of war, chat, crash recovery, race conditions, malformed input and hundreds of randomized games checked against an independent model of the rules. Then inspects every screen on desktop and phones (including iPhone's WebKit engine) side by side with the version before the change, to catch layout problems and anything that now looks or behaves oddly. Use this whenever the user wants to test the game, playtest, check for regressions, asks "did I break anything", "is it safe to ship/deploy/merge", "does it look right on mobile", or has just changed game rules, the API, the client, the CSS or persistence in this repo, even if they only say "test it" or "make sure it still works".
---

# Playtest Tank Tactics

The goal is a trustworthy answer to one question: **is this version safe to ship?** A green result has to mean the game really works, and a red one has to point at a real problem, so the value of this skill lies in reporting honestly what was and wasn't exercised.

There are two harnesses. `scripts/playtest.py` checks that the game *works*; `scripts/inspect_ui.py` checks that it *looks and feels right* on a PC and on phones. Changes have passed every functional test yet left the phone page unscrollable or hid controls, so both matter. `playtest.py` never imports the game code. It starts the real server on a temporary database and port, so it cannot touch `data/tank-tactics.db` or any deployed game, and it deletes its files when finished.

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

Exit code 0 means all passed, 1 means failures, 2 means the harness couldn't run. A browser that can't launch makes scenarios **SKIPPED**, which is not a pass: say so in the report, because the browser scenarios are the main coverage of `public/js/`.

**4. Inspect the UI on PC and phones**, in the background alongside step 3 if you can (about 2 minutes):

```bash
.venv/Scripts/python .claude/skills/playtest/scripts/inspect_ui.py --out <scratchpad>/ui
```

It starts the current working tree and a baseline (by default, where this branch left `origin/staging`) side by side, sets up the same games on both, and captures every screen: home, join, each lobby tab, rules, each game tab, the action sheets, a dead player with the jury, fog of war, game over and a closed lobby. It does this on `desktop` (1280x800), `iphone` (390x664, WebKit), `small-android` (320x568) and `iphone-landscape` (667x375). Use `--devices` / `--only` to narrow it, `--baseline <ref>` to compare with something else, and `--current <ref>` to replay an old version. Exit code 0 means no new findings, 1 means new findings, 2 means it couldn't run.

Read `<out>/report.md` first. It has three parts:

- **New findings**: automatic layout checks that fail now but passed in the baseline. They cover controls that are off screen, covered, or only reachable by scrolling something the user can't scroll; a page locked from scrolling, or one that on a phone only scrolls inside a box (`inner-scroll-only`, which on a real iPhone often means "I can't scroll"); sideways scroll; clipped text; a top bar that scrolls away; tap targets under 30px; text fields under 16px (iOS zooms in when you tap them and stays zoomed); junk text; and console or JavaScript errors.
- **Fixed** and **pre-existing** findings, summarised by type.
- **Every screen, ranked by how much it changed visually**, linking to a side-by-side image (baseline | current) and, when the page is taller than the screen, a whole-page image.

The checks are heuristics. The real job here is to **look at the pictures like a player would.** Open the side-by-side images with the Read tool: every screen with a new finding, every screen the ranking shows as changed beyond the game-board noise (tank positions are random, so game screens always differ a little), and every `iphone` screen when the change touches `public/`. For each one, ask what a player would find odd, especially anything that **wasn't there before**:

- Something has gone missing: the title, a button, a tab, part of a form, the game code.
- Things are cut off, overlap, are covered by a sticky bar or sheet, or sit off the edge of the screen.
- The page can't scroll but there's more below, or it scrolls when it clearly shouldn't (sideways, a few stray pixels).
- Spacing, alignment, sizes or colours changed in ways the diff didn't intend: a top bar that suddenly wraps onto two rows, a big empty gap, text that got tiny, a button that lost its style.
- Screens that disagree with each other for no reason, or a desktop regression caused by a phone fix (and the other way round).
- Text that reads wrong: stale numbers, "1 players", leftover debug wording.

Compare against the diff. A change the user made on purpose is not a finding, but a side effect on a screen they didn't touch usually is. Oddities the baseline already had are worth one line under "Pre-existing"; don't let them drown out what's new. The emulator is not a real phone (no browser toolbars, no real touch scrolling, no system text size), so treat `inner-scroll-only` and `page-locked` on phones as serious even when the screenshot looks fine, and for layout changes say that a check on a real phone is still worth doing.

**5. Triage every failure before reporting it.** For each one, decide which it is:

- **Game bug:** the behaviour contradicts `RULES.md`, `NOTES.md` or the invariants in `CLAUDE.md`. Reproduce it with `--only <scenario>` and find the cause in the code.
- **Intentional change:** the user changed this rule on purpose, so the scenario is now out of date. Confirm against their diff and `NOTES.md`, then update the scenario (and the `Model` class, if a rule changed) and mention that you did.
- **Flaky or harness problem:** re-run with the same seed. If it only sometimes fails, report it as flaky rather than dismissing it, since races in this game are real bugs.

Don't weaken a check just to get a green run. A check that's in the way is usually telling you something.

**6. Only report; don't fix** unless the user asked for fixes. They asked whether it broke, and they may want to decide how to handle it.

## Keeping it current

When a feature is added, add a scenario so future runs protect it. When the client gains a new screen or state, add a group to `GROUPS` in `inspect_ui.py` so it gets photographed too: a group is a short generator that drives the page and yields a screen name whenever there's something to capture. Scenarios are short functions registered with `@scenario("area")`; copy a neighbour. If a rule changes, update `Model.apply` as well, deriving it from the rules and not from the new code, because its independence is what lets it catch mistakes.

Google sign-in can't be played end to end here (it needs real Google tokens). The harness only checks that it is safely off without a client ID; `tests/backend/test_auth.py` covers the rest with a fake verifier.

## Report format

Start with the verdict, then keep it short:

```
**Safe to ship** / **Not safe to ship: N problems**

Tested: pytest X passed; playtest Y/Z scenarios, N checks, M random actions (seed S);
        UI: N screens on desktop + 3 phone sizes vs <baseline>, K images looked at
Not tested: anything skipped, or "nothing skipped"

Problems (most serious first)
1. What a player would experience. Cause at file:line. How to reproduce.

Looks odd (new since <baseline>)
- device / screen: what looks wrong, in a player's words, with the image path.

Pre-existing oddities: one line each, only if worth knowing

Outdated scenarios updated: only if any
```

Give image paths in full so the user can open them, and embed the one or two most telling images in the reply.

Describe problems by their effect on players ("two players can end up on one tile", "on an iPhone you can't scroll to Advanced rules"). Rank anything that leaks hidden information, loses saved games or crashes the server highest, then anything that stops a player doing something (an unreachable control), then cosmetic oddities. A UI problem that stops someone creating, joining or playing a game makes the version **not safe to ship**.

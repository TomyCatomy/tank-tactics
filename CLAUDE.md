# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Tank Tactics: a slow, real-time multiplayer strategy game (one tank per player, AP accrues over real time, last tank standing wins). The original spec is the source of truth for game rules; `RULES.md` is the player-facing version and `NOTES.md` records every ambiguity that was resolved and how. Check `NOTES.md` before changing rule behaviour.

## Commands

The project uses a local venv at `.venv` (Windows paths shown; on macOS/Linux use `.venv/bin/python`).

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt

.venv/Scripts/python -m tank_tactics.server          # http://localhost:8080, API docs at /docs
.venv/Scripts/python -m pytest -q                    # all tests
.venv/Scripts/python -m pytest -q tests/backend      # or tests/frontend (browser tests)
.venv/Scripts/python -m pytest -q tests/backend/test_game.py::test_shoot_costs_one_ap_and_deals_one_damage
.venv/Scripts/python -m pytest -q -k fog             # by keyword
.venv/Scripts/python -m pytest -q -m "not ui"         # skip the (slower) browser tests
.venv/Scripts/python -m pyflakes tank_tactics scripts tests   # lint (pyflakes is not pinned in requirements)
.venv/Scripts/python scripts/simulate.py --players 12 --runs 100 --quiet   # bot balance simulation
# CI: .github/workflows/tests.yml runs the whole suite (browser tests included) on every push
# AWS deploy (EC2 + CloudFront): GitHub Actions > "AWS deploy" > Run workflow on main; see deploy/aws/README.md
```

Server settings come from environment variables read by `ServerConfig` (pydantic-settings): `PORT`, `DB_PATH` (default `data/tank-tactics.db`), `HOST`, rate limits, etc. For quick manual play, run with `ALLOW_DEBUG=true` and create a game with `debug.fast_ticks` on so AP arrives every few seconds instead of on the schedule (every 24 h by default). `ALLOW_DEBUG` defaults to off, which hides the `debug.*` options and makes the API reject them; `make_service` turns it on for tests.

The frontend (`public/`) is plain JS with no build step and is served by the API. Third-party scripts are vendored as release files in `public/vendor/` (currently rrule.js, used by the lobby's schedule editor to parse and write rules). There is no JS tooling in the repo; the client is tested through the browser tests below. The client is native ES modules under `public/js/` (entry `js/main.js`, loaded with `type="module"` after the classic rrule.js script): `dom.js`, `api.js`, `storage.js` and `format.js` are shared helpers, `pages/` holds home and join, and `game/` the game page, whose state lives in `game/session.js` as the live-bound `G`. Modules import each other's render functions directly; the import cycles are fine because nothing runs at import time except `main.js`. Build DOM with `h()` / `fill()` from `dom.js`; never pass possibly-`null` children to `replaceChildren()`, which renders them as the text "null".

## Architecture

Layers, from the inside out. Each layer only depends on the ones above it:

0. **`tank_tactics/recurrence/`**: pure RFC 5545 RRULE support for the AP schedule (`python-dateutil` + `zoneinfo`), re-exported from its `__init__`: `rules` (parsing with dateutil's `rrulestr` plus the game's own checks), `zones`, `cadence` and `sampling` (how often a rule may fire), `schedule` (cached `Schedule` objects: `next_after`, `between`, `upcoming`, DST handling) and `describe` (summaries). It imports nothing from the rest of the project, so `config` can validate with it.
1. **`tank_tactics/config.py`**: `GameConfig` is a strict, frozen pydantic model split into sections (`board`, `start`, `ap`, `actions`, `jury`, `players`, `debug`, `notifications`, plus top-level `fog_of_war` and `max_duration_seconds`). Lobby overrides are partial nested dicts deep-merged by `with_overrides`; unknown keys and type coercion (`"5"`→`5`) are rejected. `ServerConfig` is the process config.
2. **`tank_tactics/game/`**: pure rules engine with no I/O, clock or global randomness. Callers pass `now` (epoch seconds) and a `random.Random`. Public API is re-exported from `game/__init__.py`, and callers use `from tank_tactics import game` then `game.move(...)`.
3. **`tank_tactics/store.py`**: SQLAlchemy 2.0 ORM over SQLite, which converts between rows and the `game` dataclasses.
4. **`tank_tactics/service.py`**: `GameService` keeps live (lobby/active) games in memory, reads finished/closed ones from the DB on demand through a small LRU cache (`archive_cache_size`; use `service.get(id)`, not `service.games`, for any game), and is the only place state changes are committed.
5. **`tank_tactics/api/`**: FastAPI app (`create_app`), with routers in `api/routes/` (one module per area), the WebSocket `Hub`, the tick scheduler and Discord notifications. `server.py` only runs uvicorn.

### Invariants that span several files

- **Validate, then mutate.** Every rule function in `game/` raises `GameError` before touching state, and returns the `LogEntry` list it appended. On top of that, `GameService._mutate` runs each change on a `deepcopy` under a per-game `threading.Lock`, persists it with `store.save` in one transaction, and only then swaps the copy in. New state-changing operations must go through `_mutate`.
- **Fog of war is enforced server-side at write time and read time.** Each log entry gets a `visibility` (`public` / `participants` / `actor_only`) when it is written (`events.fogged`), and every payload sent to a client (HTTP *and* WebSocket) is built by `GameService.view` → `game.view_for(state, viewer_id)`, which drops hidden entries and other players' AP. Never serialise `GameState` directly. Tests in `tests/backend/test_game.py` and `test_api.py` assert on the actual payloads.
- **Time is event-driven, not polled per player.** `game.clock.advance_time(state, now)` applies every due AP tick, jury close and time limit in chronological order. AP ticks fall on the occurrences of `ap.rrule` in `ap.timezone` from DTSTART = `started_at` (`game/recurrence.py` binds the schedule to a game; `GameConfig.effective_schedule` swaps in the debug interval), and the jury closes on the same occurrences, after the tick. It powers both the live scheduler (`api/background.py`, which sleeps until `next_due_at` or until the Hub's `wake` event fires on any change) and restart catch-up (the first scheduler pass after boot). `service.act` also calls it before each action.
- **Threading model:** FastAPI endpoints are plain `def`, so they run in the thread pool. The service is thread-safe, and its `on_change` callback is hooked by `api/hub.py`, which hops onto the event loop with `call_soon_threadsafe` to broadcast a per-viewer state push to each WebSocket.
- **Player actions** are a pydantic discriminated union on `type` (`game/commands.py`: `ActionType` plus one `*Command` model per action, each with `apply()`). FastAPI validates `/action` bodies against the same union. Shape validation lives there; game rules stay in `game/actions.py`.
- **Schedule validation** has one source of truth: `recurrence.validate`. New rules must be on round minutes, at most once a minute (no `FREQ=SECONDLY`) and fire in every calendar month (35 days at most); see NOTES.md 28 and 35. `ALLOW_DEBUG` doesn't loosen the schedule: tests that need sub-minute ticks use `debug.fast_ticks`. Every scheduled event, including the time limit (rounded up), is on a whole minute except under fast ticks. `tests/support.py`'s `T0` is a round minute for that reason.
- **Log entries** use `event_type: EventType` (a `StrEnum` in `game/state.py`); players use `action_range` (not `range`).

### Wire format is coupled to `public/js/`

The client reads the JSON from `view_for` and the config shape directly: player keys (`action_range`, `ap` only when visible), log keys (`event_type`, `details.target_hp_after`, `game_start.details`), and the nested config paths used by the lobby form's `FIELDS` list (dotted keys such as `jury.enabled`; the schedule editor writes `ap.rrule` and `ap.timezone` and reads `game.schedule`). Renaming any of these requires updating the client (`FIELDS` is in `js/config-form.js`); `tests/backend/test_api.py::test_payload_field_names` pins the main ones. The WebSocket protocol: the client's first message is `{"type": "auth", "token": ...}`, then the server pushes `{"type": "state", "state": <view>}`.

All API errors use `{"error": {"code", "message"}}` (`api/errors.py` also converts FastAPI validation errors to 400s in this format).

### Tests

Two suites, each with its own `conftest.py` (fixtures). Plain helpers shared by both live in `tests/support.py`: `make_active(positions, **config_sections)` builds a started game with tanks at fixed tiles (e.g. `make_active({"a": (0, 0), "b": (1, 1)}, jury={"enabled": True})`), `make_service()` gives a `GameService` with an injectable `Clock`, plus `setup_game` and `place`.

- `tests/backend/`: `test_game.py` (pure rules), `test_service.py` (persistence and restart catch-up on a real SQLite file via `tmp_path`, concurrency with threads and a barrier, chat privacy), `test_api.py` (the `api` fixture: FastAPI `TestClient`, needs `httpx2`; includes WebSocket pushes).
- `tests/frontend/`: Playwright drives the real page in headless Edge (or bundled Chromium) against uvicorn in a thread. The conftest provides `server` (a `LiveServer` that can `stop()`/`start()` to simulate outages), `browser` and `page`, and marks every test here `ui`. This is the only coverage of `public/js/`, so add a test here for client bugs. Skipped if no browser can be launched.

## Code size

A Python file is at most 150 lines unless there's a good reason for it to be longer; if there is one, say what it is in the file's docstring. Split a module that would grow past that into a package of focused modules that re-exports its public API from `__init__.py`, as `tank_tactics/recurrence/` does.

## Workspaces and branches

- Each new feature request in a thread starts in its own workspace: before changing any files, create a separate git worktree checked out to a new feature branch (for example `feature/<short-name>`) based on the latest `origin/main`, and do all the work for that request there. Never make feature changes on `main` or in the main checkout.
- Follow-up fixes and tweaks to that feature stay in the same workspace and branch (and are folded in as described under Commits). A different, unrelated feature request in the same thread gets its own new workspace and branch.
- The `.venv` is not shared with a new worktree: either create one there with the setup command above, or run the main checkout's interpreter by its absolute path from inside the worktree.

## Commits

The goal is that the set of commits that gets merged is coherent and doesn't go in circles (no "add X", "fix X", "tweak X" chains).

- Every commit is one atomic change and passes the tests on its own, unless the user explicitly says otherwise for that commit.
- On a branch that hasn't been merged yet, a fix or tweak the user asks for to work already on that branch is folded into the commit it changes (for example `git commit --fixup=<sha>` followed by a non-interactive `git rebase --autosquash`), not added as a new commit. A request that is genuinely a new atomic change of its own gets its own commit.
- After a rewrite, check every rewritten commit: the full suite on the branch tip, and `pytest -q -m "not ui"` on each commit before it (for example with `git rebase --exec`).
- If folding it in would get messy (conflicts across later commits, a commit that has to be split, merge commits on the branch, and so on), ask the user before falling back to a separate commit or any other exception.
- Rewriting an unmerged branch that has already been pushed means force-pushing it. Use `git push --force-with-lease`, and only on that branch.

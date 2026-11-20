# Tank Tactics

A slow, socially intense multiplayer strategy game. Every player has one tank on a
shared grid and earns **1 action point per day**. Spend AP whenever you like to move,
shoot, upgrade range, or gift AP/HP to other tanks. Make alliances, break them, and be
the last tank standing.

Player-facing rules: [RULES.md](RULES.md). Ambiguities I resolved and how: [NOTES.md](NOTES.md).

## Stack

- **Backend:** Python 3.11+ (developed on 3.13), [FastAPI](https://fastapi.tiangolo.com/) on uvicorn for
  REST + WebSocket, pydantic / pydantic-settings for configuration, SQLAlchemy 2.0 (ORM)
  over SQLite for persistence. A single process.
- **Frontend:** plain HTML/CSS/JS (no build step) in `public/`, rendering the board as SVG.
  Mobile-friendly: responsive layout, tap targets ≥ 44px, no hover-only interactions.

```
tank_tactics/
  config.py    every configurable value + validation  ← the config file
  game/        pure game logic, no I/O (public API re-exported from game/__init__.py)
    state.py     data model: GameState, Player, LogEntry, JuryVote, GameError
    board.py     geometry, occupancy, player lookups
    guards.py    shared validation checks
    events.py    log entries and AP gains
    outcome.py   death and winning
    lobby.py     create, join, configure, remove, start
    actions.py   move, shoot, give AP/HP, upgrade range
    commands.py  ActionType enum + one typed command model per action
    jury.py      the optional jury
    clock.py     AP ticks, jury cycles, time limit, restart catch-up
    view.py      per-viewer fog-of-war redaction
  store.py     SQLAlchemy persistence (SQLite)
  service.py   per-game mutex, write-through persistence, rate limits, chat
  api/         FastAPI app (create_app in api/__init__.py)
    routes/      games.py, lobby.py, play.py, chat.py, live.py (WebSocket)
    deps.py      service + authenticated-player dependencies
    schemas.py   request / response bodies
    errors.py    uniform {"error": {code, message}} responses
    hub.py       WebSocket connections and per-viewer broadcast
    background.py  the tick scheduler
    notify.py    Discord webhook (public events only)
  server.py    entry point: runs the app with uvicorn
public/        the web client (index.html, app.js, style.css)
scripts/simulate.py   bot simulation at accelerated time
tests/
  support.py   helpers shared by both suites
  backend/     rules, service, persistence, HTTP/WebSocket API (conftest: `api` client)
  frontend/    browser tests of the web client (conftest: live server, browser, page)
```

## Run it

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -r requirements-dev.txt

python -m tank_tactics.server        # http://localhost:8080
```

Open the page, create a game, and share the invite link. Each player gets a personal
**rejoin link** in the lobby (it contains their secret token) for switching devices.

To try a game quickly, tick **Debug: fast ticks** when creating it: AP then arrives every
`debug.tick_seconds` (10 s by default) instead of every 24 hours. Two browser windows
(one private) are enough to play against yourself.

### Server settings (environment variables)

`ServerConfig` in [`tank_tactics/config.py`](tank_tactics/config.py) is a pydantic-settings
model: each field reads the environment variable of the same name (case-insensitive) and
is validated on startup.

| Variable | Default | Meaning |
|---|---|---|
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8080` | HTTP port |
| `DB_PATH` | `data/tank-tactics.db` | SQLite file (created on first run) |
| `ACTION_RATE_PER_SECOND` | `5` | Per-player action rate limit |
| `CHAT_RATE_PER_10S` | `20` | Per-player chat rate limit |
| `SCHEDULER_MAX_SLEEP` | `5` | Max seconds between scheduler checks |
| `MAX_MESSAGE_LENGTH` | `1000` | Chat message length limit |

Put it behind a TLS-terminating reverse proxy (Caddy, nginx) for real use; the client
switches to `wss://` automatically on HTTPS.

## Configure the rules

All game rules live in the `GameConfig` pydantic model in [`tank_tactics/config.py`](tank_tactics/config.py),
split into section sub-models. Each field has its default, bounds and description in one
place. The models are strict (no `"5"` → `5` coercion), immutable, and reject unknown keys.
The host can override any value per game in the lobby. Overrides are partial and nested,
for example `{"jury": {"enabled": true}}`, and are validated through the same model.

| Key | Default | |
|---|---|---|
| `board.size` | `null` (auto) | auto = `max(board.min_size, ceil(sqrt(players) × board.size_multiplier))` |
| `board.min_size` / `board.size_multiplier` | `10` / `3` | |
| `board.wreckage_blocks_tile` | `false` | |
| `start.hp` / `start.ap` / `start.action_range` | `3` / `1` / `2` | every tank's starting stats |
| `ap.interval_seconds` | `86400` | +1 AP to every living tank per interval |
| `ap.cap` | `null` | no cap |
| `actions.range_upgrade_cost` | `3` | |
| `actions.max_range` | `null` | unlimited |
| `actions.allow_give_hp` | `true` | |
| `actions.loot_on_kill` | `true` | killer takes the victim's AP |
| `jury.enabled` | `false` | |
| `jury.interval_seconds` | `86400` | |
| `jury.tie_rule` | `"all"` | or `"random"` |
| `jury.votes_public` | `true` | |
| `fog_of_war` | `"off"` | or `"alliance"` |
| `max_duration_seconds` | `null` | optional time limit |
| `players.minimum` / `players.maximum` | `2` / `64` | a game never starts with < 2 |
| `debug.fast_ticks` / `debug.tick_seconds` | `false` / `10` | testing aid |
| `notifications.discord_webhook_url` | `null` | posts public events only |

## Tests

```bash
python -m pytest              # everything, including browser tests
python -m pytest -m "not ui"  # skip the browser tests
```

The browser tests (`tests/frontend/`) drive the real page with Playwright. They use an
installed Microsoft Edge, or Playwright's Chromium (`python -m playwright install chromium`),
and are skipped if neither is available.

81 tests cover every item in the spec's testing section: Chebyshev range, move/shoot/give/
upgrade validation, kills and loot, spectators vs. jurors, the win condition, AP tick
catch-up after downtime (including a real restart against an SQLite file), the jury
(single vote, re-voting, both tie rules, no votes, and that nothing jury-related happens
when it's disabled), concurrent conflicting actions from two threads, rate limiting, chat
privacy, and fog of war checked on the actual HTTP and WebSocket payloads.

## Simulation

```bash
python scripts/simulate.py --players 12 --seed 7            # one game, prints the full log
python scripts/simulate.py --players 20 --jury --fog alliance
python scripts/simulate.py --players 12 --runs 200 --quiet  # aggregate stats only
```

Bots with four personalities (aggressive, hoarder, diplomat, coward) play a full game on
the real rules engine with a virtual clock (AP interval = 1 second), so it finishes
instantly. Use `--runs` to compare win rates when tuning the config.

## API (summary)

Interactive docs are served at `/docs` (OpenAPI schema at `/openapi.json`).
All state-changing requests use `Authorization: Bearer <player token>`.

| Method & path | Body | |
|---|---|---|
| `GET /api/defaults` | | default config |
| `POST /api/games` | `{name, config?}` | create; returns `{game_id, player_id, token}` |
| `GET /api/games/{id}` | | public lobby info for the invite page |
| `POST /api/games/{id}/join` | `{name}` | lobby only |
| `GET /api/games/{id}/state` | | the caller's redacted view |
| `POST /api/games/{id}/config` | `{config}` | host, lobby only |
| `POST /api/games/{id}/start` | | host |
| `POST /api/games/{id}/remove` | `{player_id}` | host; mid-game this destroys the tank |
| `POST /api/games/{id}/leave` | | lobby only, not the host; your token stops working |
| `POST /api/games/{id}/close` | | host, lobby only; cancels the game for everyone (status `closed`) |
| `POST /api/games/{id}/action` | `{type: move, to:[x,y]}` · `{type: shoot, target_id}` · `{type: give_ap\|give_hp, target_id, amount}` · `{type: upgrade}` | |
| `POST /api/games/{id}/vote` | `{candidate_id}` | jurors, jury enabled only |
| `POST /api/games/{id}/chat` | `{to_id: null\|player_id, text}` | |
| `WS /ws/{id}` | first message `{type: auth, token}` | pushes `{type: state, state}` on every change |

Errors come back as `{"error": {"code", "message"}}` with a 4xx status.

## How the guarantees are met

- **Server is the source of truth.** The client only sends intents. Every action
  re-validates game status, the actor being alive, AP, the target, range, and the
  destination on the server.
- **Fog of war is enforced on the server.** Each log entry is tagged `public`,
  `participants` or `actor_only` when written, and every payload (HTTP and WebSocket)
  goes through `view_for(state, viewer)`, which drops hidden entries and other
  players' AP. The client never receives hidden data.
- **Atomic actions, no races.** Each game has a mutex. An action runs on a copy of
  the state, is committed to SQLite in one transaction, and only then replaces the
  live state. If two players shoot the same last-HP tank at once, one succeeds and the
  other gets a clean `invalid_target` error.
- **Ticks survive restarts.** `last_ap_tick_at` and `last_jury_cycle_at` are stored.
  On boot the scheduler applies every whole interval that elapsed, in chronological
  order with jury closes and the time limit.

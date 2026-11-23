#!/usr/bin/env python
"""Black-box playtester for Tank Tactics.

Boots the REAL server as a subprocess on a throwaway database and plays it the way
clients do: HTTP, WebSocket and a real browser. Nothing here imports the game code,
so the rules below are an independent second opinion on the implementation.

    .venv/Scripts/python .claude/skills/playtest/scripts/playtest.py            # everything
    ... playtest.py --quick                 # no browser, short fuzz (about 1 min)
    ... playtest.py --only fog,jury         # some areas
    ... playtest.py --seed 123              # reproduce a fuzz failure
    ... playtest.py --list                  # show every scenario

Exit code: 0 = all passed, 1 = something failed, 2 = the harness itself could not run.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[4]

try:
    import httpx
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.client import connect as ws_connect
except ImportError as e:  # pragma: no cover
    print(f"Missing dependency ({e}). Run: .venv/Scripts/python -m pip install -r requirements-dev.txt")
    sys.exit(2)

YEAR = 365 * 86_400
QUIET = {"ap": {"interval_seconds": YEAR}}  # no AP ticks unless a scenario wants them
RICH = {"start": {"ap": 60, "hp": 3, "action_range": 200}, **QUIET}


def merge(*dicts: dict) -> dict:
    out: dict = {}
    for d in dicts:
        for k, v in (d or {}).items():
            out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def dist(a, b) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


# -- server ------------------------------------------------------------------------------

class Server:
    """The real server process, on its own port and database file."""

    def __init__(self, workdir: Path, name: str, **env: str):
        self.name, self.dir = name, workdir / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db, self.logfile = self.dir / "game.db", self.dir / "server.log"
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.url, self.ws_url = f"http://127.0.0.1:{self.port}", f"ws://127.0.0.1:{self.port}"
        self.env = {
            "HOST": "127.0.0.1", "PORT": str(self.port), "DB_PATH": str(self.db), "SCHEDULER_MAX_SLEEP": "0.2",
            "ACTION_RATE_PER_SECOND": "100000", "CHAT_RATE_PER_10S": "1000000", "LOGIN_RATE_PER_MINUTE": "100000",
            "PYTHONUNBUFFERED": "1", "ALLOW_DEBUG": "true", **env,
        }
        self.proc: subprocess.Popen | None = None
        self.http: httpx.Client | None = None

    def start(self) -> None:
        env = {k: v for k, v in os.environ.items() if k.upper() != "GOOGLE_CLIENT_ID"}
        env.update(self.env)
        log = open(self.logfile, "ab")
        self.proc = subprocess.Popen([sys.executable, "-m", "tank_tactics.server"], cwd=ROOT, env=env,
                                     stdout=log, stderr=subprocess.STDOUT)
        log.close()
        self.http = httpx.Client(base_url=self.url, timeout=20, trust_env=False)
        deadline = time.monotonic() + 30
        while True:
            try:
                if self.http.get("/api/defaults").status_code == 200:
                    return
            except httpx.TransportError:
                pass
            if self.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"server failed to start:\n{self.log_tail()}")
            time.sleep(0.05)

    def crash(self) -> None:
        """Kill without any shutdown, like a power cut."""
        if self.http:
            self.http.close()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(10)

    def stop(self) -> None:
        self.crash()

    def log_tail(self, n: int = 25) -> str:
        try:
            return "\n".join(self.logfile.read_text(errors="replace").splitlines()[-n:])
        except OSError:
            return ""

    def tracebacks(self) -> int:
        try:
            blocks = self.logfile.read_text(errors="replace").split("Traceback (most recent call last)")[1:]
        except OSError:
            return 0
        # A browser tab closing mid-request is normal on Windows, not a server bug.
        return sum("ConnectionResetError" not in b[:3000] for b in blocks)


class Seat:
    def __init__(self, creds: dict, name: str):
        self.game_id, self.id, self.token, self.name = creds["game_id"], creds["player_id"], creds["token"], name


# -- scenario context --------------------------------------------------------------------

class Failed(Exception):
    """Stops a scenario whose remaining steps depend on a failed check."""


class T:
    """One scenario's helpers. Checks record a failure and carry on where they safely can."""

    def __init__(self, h: "Harness", name: str):
        self.h, self.name, self.srv, self.failures, self.checks = h, name, h.main, [], 0
        self.rng = random.Random(f"{h.seed}:{name}")
        self._lock = threading.Lock()

    def fail(self, msg: str) -> None:
        with self._lock:
            self.failures.append(msg)

    def ok(self, cond: Any, msg: str) -> bool:
        with self._lock:
            self.checks += 1
        if not cond:
            self.fail(msg)
        return bool(cond)

    def eq(self, got: Any, want: Any, msg: str) -> bool:
        return self.ok(got == want, f"{msg}: expected {want!r}, got {got!r}")

    def must(self, cond: Any, msg: str) -> None:
        if not self.ok(cond, msg):
            raise Failed(msg)

    # http ----------------------------------------------------------------------
    def req(self, method: str, path: str, seat: Seat | str | None = None, srv: Server | None = None, **kw) -> httpx.Response:
        srv = srv or self.srv
        token = seat.token if isinstance(seat, Seat) else seat
        headers = kw.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = srv.http.request(method, path, headers=headers, **kw)
        if r.status_code >= 500:
            self.fail(f"server error {r.status_code} on {method} {path} body={str(kw)[:160]!r}: {r.text[:160]}")
        return r

    def post(self, seat: Seat, what: str, body: Any = None, **kw) -> httpx.Response:
        args = {} if body is None else {"json": body}
        return self.req("POST", f"/api/games/{seat.game_id}/{what}", seat, **args, **kw)

    def act(self, seat: Seat, type_: str, **body) -> httpx.Response:
        return self.post(seat, "action", {"type": type_, **body})

    def good(self, r: httpx.Response, what: str) -> httpx.Response:
        self.must(r.status_code < 300, f"{what}: expected success, got {r.status_code} {r.text[:200]}")
        return r

    def err(self, r: httpx.Response, status: int, code: str | None, what: str) -> None:
        got = None
        try:
            got = r.json()["error"]
            shaped = isinstance(got["code"], str) and isinstance(got["message"], str)
        except (ValueError, KeyError, TypeError):
            shaped = False
        self.ok(r.status_code == status and shaped and (code is None or got["code"] == code),
                f"{what}: expected {status} {code or ''}, got {r.status_code} {r.text[:180]}")

    # games ---------------------------------------------------------------------
    def lobby(self, n: int = 2, cfg: dict | None = None, srv: Server | None = None) -> list[Seat]:
        r = self.good(self.req("POST", "/api/games", srv=srv, json={"name": "P0", "config": cfg}), "create game")
        seats = [Seat(r.json(), "P0")]
        for i in range(1, n):
            j = self.good(self.req("POST", f"/api/games/{seats[0].game_id}/join", srv=srv, json={"name": f"P{i}"}), "join")
            seats.append(Seat(j.json(), f"P{i}"))
        return seats

    def game(self, n: int = 2, cfg: dict | None = None, srv: Server | None = None) -> list[Seat]:
        seats = self.lobby(n, merge(RICH, cfg or {}), srv)
        self.good(self.req("POST", f"/api/games/{seats[0].game_id}/start", seats[0], srv=srv), "start game")
        return seats

    def view(self, seat: Seat, srv: Server | None = None) -> dict:
        return self.good(self.req("GET", f"/api/games/{seat.game_id}/state", seat, srv=srv), "read state").json()

    def tank(self, seat: Seat, of: Seat | None = None, srv: Server | None = None) -> dict:
        v = self.view(seat, srv)
        return next(p for p in v["players"] if p["id"] == (of or seat).id)

    def walk_to_distance(self, mover: Seat, other: Seat, d: int) -> None:
        for _ in range(250):
            v = self.view(mover)
            me = next(p for p in v["players"] if p["id"] == mover.id)
            o = next(p for p in v["players"] if p["id"] == other.id)
            a, b = (me["x"], me["y"]), (o["x"], o["y"])
            if dist(a, b) == d:
                return
            blocked = {(p["x"], p["y"]) for p in v["players"] if p["alive"]}
            options = [(a[0] + dx, a[1] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
            options = [t for t in options if 0 <= t[0] < v["game"]["board_size"] and 0 <= t[1] < v["game"]["board_size"]
                       and t not in blocked]
            self.must(options, "walk: tank is boxed in")
            best = min(options, key=lambda t: abs(dist(t, b) - d))
            self.good(self.act(mover, "move", to=list(best)), "walk")
        raise Failed("walk: never reached the wanted distance")

    def wait(self, fn: Callable[[], Any], timeout: float, what: str) -> Any:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = fn()
            if got:
                return got
            time.sleep(0.05)
        self.fail(f"timed out after {timeout}s waiting for {what}")
        raise Failed(what)


SCENARIOS: list[tuple[str, str, Callable[[T], None], set[str]]] = []


def scenario(area: str, *tags: str):
    def deco(fn):
        SCENARIOS.append((area, fn.__name__, fn, set(tags)))
        return fn
    return deco


# == lobby ===============================================================================

@scenario("lobby")
def names_are_validated(t: T):
    def create(name):
        return t.req("POST", "/api/games", json={"name": name})
    for bad in ("", "   ", "x" * 25):
        t.err(create(bad), 400, "invalid_name", f"create with name {bad!r}")
    for bad in (None, 5, True, [], {}):
        t.err(create(bad), 400, None, f"create with non-string name {bad!r}")
    t.good(create("x" * 24), "24-character name")
    t.good(create("🙂 Tänk 坦克"), "unicode name")
    host = Seat(create("  Spaced    Out ").json(), "x")
    t.eq(t.view(host)["players"][0]["name"], "Spaced Out", "name whitespace is collapsed")
    join = lambda n: t.req("POST", f"/api/games/{host.game_id}/join", json={"name": n})
    t.err(join("SPACED out"), 400, "invalid_name", "duplicate name differing only by case")
    t.good(join("Other"), "unique name")
    raw = json.dumps({"name": "bad\ud800name"}).encode()  # hostile unicode must never be a 500
    r = t.req("POST", "/api/games", content=raw, headers={"content-type": "application/json"})
    t.ok(r.status_code < 500, f"name with a broken unicode character crashed the server: {r.status_code}")


@scenario("lobby")
def lobby_capacity_and_minimum(t: T):
    s = t.lobby(2, {"players": {"minimum": 2, "maximum": 2}})
    t.err(t.req("POST", f"/api/games/{s[0].game_id}/join", json={"name": "Late"}), 400, "lobby_full", "join a full lobby")
    info = t.req("GET", f"/api/games/{s[0].game_id}").json()
    t.eq((info["player_count"], info["max_players"], info["host"], info["status"]), (2, 2, "P0", "lobby"), "public info")
    t.ok("token" not in json.dumps(info), "public info contains no tokens")

    solo = t.lobby(1)
    t.err(t.post(solo[0], "start"), 400, "not_enough_players", "start alone")
    three = t.lobby(2, {"players": {"minimum": 3}})
    t.err(t.post(three[0], "start"), 400, "not_enough_players", "start below configured minimum")
    tiny = t.lobby(10, {"board": {"size": 3}})
    t.err(t.post(tiny[0], "start"), 400, "board_too_small", "10 players on a 3x3 board")
    t.eq(t.view(tiny[0])["game"]["status"], "lobby", "failed start stays in the lobby")
    packed = t.lobby(9, {"board": {"size": 3}})
    t.good(t.post(packed[0], "start"), "9 players fill a 3x3 board")
    v = t.view(packed[0])
    t.eq(len({(p["x"], p["y"]) for p in v["players"]}), 9, "every tank has its own tile")


@scenario("lobby")
def host_only_operations(t: T):
    h, b, c = t.lobby(3)
    t.err(t.post(b, "start"), 400, "forbidden", "non-host start")
    t.err(t.post(b, "close"), 400, "forbidden", "non-host close")
    t.err(t.post(b, "config", {"config": {"fog_of_war": "alliance"}}), 400, "forbidden", "non-host config")
    t.err(t.post(b, "remove", {"player_id": c.id}), 400, "forbidden", "non-host remove")
    t.err(t.post(h, "remove", {"player_id": h.id}), 400, "invalid_target", "host removes self")
    t.err(t.post(h, "remove", {"player_id": "nope"}), 400, "not_found", "remove unknown player")
    t.err(t.post(h, "leave"), 400, "forbidden", "host leaves")
    t.good(t.post(h, "remove", {"player_id": c.id}), "host removes player in lobby")
    t.err(t.req("GET", f"/api/games/{c.game_id}/state", c), 401, "unauthorized", "removed player's token is revoked")
    t.eq(len(t.view(h)["players"]), 1 + 1, "player count after removal")


@scenario("lobby")
def leave_then_rejoin(t: T):
    h, b = t.lobby(2)
    t.good(t.post(b, "leave"), "leave lobby")
    t.err(t.req("GET", f"/api/games/{b.game_id}/state", b), 401, "unauthorized", "token after leaving")
    t.err(t.post(b, "leave"), 401, "unauthorized", "leave twice")
    t.eq(len(t.view(h)["players"]), 1, "player gone")
    again = t.good(t.req("POST", f"/api/games/{h.game_id}/join", json={"name": "P1"}), "rejoin with the same name")
    t.ok(again.json()["player_id"] != b.id, "rejoin gets a new seat")


@scenario("lobby")
def closed_lobby_is_final(t: T):
    h, b = t.lobby(2)
    t.good(t.post(h, "close"), "close lobby")
    t.eq(t.view(b)["game"]["status"], "closed", "player can still see it was closed")
    t.err(t.req("POST", f"/api/games/{h.game_id}/join", json={"name": "Late"}), 400, "closed", "join closed lobby")
    t.err(t.post(h, "start"), 400, "closed", "start closed lobby")
    t.err(t.post(h, "close"), 400, "closed", "close twice")
    t.err(t.post(h, "config", {"config": {"fog_of_war": "alliance"}}), 400, "closed", "configure closed lobby")
    t.err(t.post(b, "leave"), 400, "closed", "leave closed lobby")
    t.err(t.act(h, "upgrade"), 400, "closed", "act in closed lobby")


@scenario("lobby")
def lobby_locked_after_start(t: T):
    h, b, c = t.game(3)
    t.err(t.req("POST", f"/api/games/{h.game_id}/join", json={"name": "Late"}), 400, "not_lobby", "join after start")
    t.err(t.post(h, "start"), 400, "not_lobby", "start twice")
    t.err(t.post(h, "config", {"config": {"fog_of_war": "alliance"}}), 400, "not_lobby", "config after start")
    t.err(t.post(b, "leave"), 400, "not_lobby", "leave after start")
    t.err(t.post(h, "close"), 400, "not_lobby", "close after start")
    pre = t.lobby(2, QUIET)
    t.err(t.act(pre[0], "upgrade"), 400, "not_active", "act before start")


@scenario("config")
def invalid_configs_are_rejected(t: T):
    bad = [
        {"nope": 1}, {"board": {"nope": 1}}, {"board": 5}, {"board": {"size": 2}}, {"board": {"size": 201}},
        {"board": {"size": "5"}}, {"board": {"size": 5.0}}, {"board": {"size": True}}, {"start": {"hp": 0}},
        {"start": {"hp": 101}}, {"start": {"ap": -1}}, {"start": {"action_range": 0}}, {"ap": {"interval_seconds": 0}},
        {"ap": {"cap": 0}}, {"actions": {"range_upgrade_cost": 0}}, {"actions": {"max_range": 1}},
        {"actions": {"allow_give_hp": "yes"}}, {"jury": {"tie_rule": "coin"}}, {"jury": {"enabled": 1}},
        {"fog_of_war": "on"}, {"max_duration_seconds": 0}, {"players": {"minimum": 1}},
        {"players": {"minimum": 5, "maximum": 3}}, {"debug": {"tick_seconds": 0}},
        {"start": {"ap": 5}, "ap": {"cap": 4}}, {"start": {"action_range": 5}, "actions": {"max_range": 4}},
        {"notifications": {"discord_webhook_url": "http://discord.com/api/webhooks/1/x"}},
        {"notifications": {"discord_webhook_url": "https://evil.example/api/webhooks/1/x"}},
        {"notifications": {"discord_webhook_url": "https://discord.com.evil.example/api/webhooks/1/x"}},
        {"notifications": {"discord_webhook_url": "https://discord.com/not-webhooks"}},
    ]
    for cfg in bad:
        t.err(t.req("POST", "/api/games", json={"name": "A", "config": cfg}), 400, "invalid_config", f"create with {cfg}")
    h, _ = t.lobby(2)
    before = t.view(h)["game"]["config"]
    for cfg in bad:
        t.err(t.post(h, "config", {"config": cfg}), 400, "invalid_config", f"update with {cfg}")
    t.eq(t.view(h)["game"]["config"], before, "rejected updates change nothing")


@scenario("config")
def config_update_applies_and_resets_stats(t: T):
    h, b = t.lobby(2)
    t.good(t.post(h, "config", {"config": {"start": {"hp": 7, "ap": 9, "action_range": 4}}}), "update start stats")
    t.good(t.post(h, "config", {"config": {"jury": {"enabled": True}}}), "partial update")
    v = t.view(b)
    t.eq(v["game"]["config"]["start"]["hp"], 7, "earlier update survives a partial one")
    t.ok(v["game"]["config"]["jury"]["enabled"], "partial update applied")
    for p in v["players"]:
        t.eq((p["hp"], p["ap"], p["action_range"]), (7, 9, 4), "joined players get the new stats")
    t.err(t.post(t.lobby(3)[0], "config", {"config": {"players": {"maximum": 2}}}), 400, "lobby_full", "max below joined")


@scenario("config")
def webhook_is_visible_only_to_host(t: T):
    url = "https://discord.com/api/webhooks/1/secret-value"
    h, b = t.lobby(2, {"notifications": {"discord_webhook_url": url}})
    t.eq(t.view(h)["game"]["config"]["notifications"]["discord_webhook_url"], url, "host sees webhook")
    t.ok("secret-value" not in t.req("GET", f"/api/games/{b.game_id}/state", b).text, "webhook leaked to a non-host")
    t.ok("secret-value" not in t.req("GET", f"/api/games/{b.game_id}").text, "webhook leaked in public info")


@scenario("config")
def auto_board_size(t: T):
    for n, want in ((2, 10), (11, 10), (12, 11)):
        s = t.game(n)
        t.eq(t.view(s[0])["game"]["board_size"], want, f"auto board size for {n} players")


# == actions =============================================================================

@scenario("actions")
def move_rules(t: T):
    a, b = t.game(2, {"board": {"size": 12}, "start": {"action_range": 2}})
    t.walk_to_distance(a, b, 1)
    v = t.view(a)
    me = next(p for p in v["players"] if p["id"] == a.id)
    o = next(p for p in v["players"] if p["id"] == b.id)
    t.err(t.act(a, "move", to=[o["x"], o["y"]]), 400, "occupied", "move onto a tank")
    t.err(t.act(a, "move", to=[me["x"], me["y"]]), 400, "not_adjacent", "move to own tile")
    far = (me["x"] + 2 if me["x"] + 2 < 12 else me["x"] - 2, me["y"])
    t.err(t.act(a, "move", to=list(far)), 400, "not_adjacent", "move two tiles")
    for off in ([-1, 0], [0, -1], [12, 0], [0, 12], [10**30, 0]):
        t.err(t.act(a, "move", to=off), 400, "out_of_bounds", f"move off board {off}")
    t.eq(t.tank(a)["ap"], me["ap"], "rejected moves cost nothing")
    free = [(me["x"] + dx, me["y"] + dy) for dx, dy in ((1, 1), (-1, -1), (1, -1), (-1, 1))
            if 0 <= me["x"] + dx < 12 and 0 <= me["y"] + dy < 12 and (me["x"] + dx, me["y"] + dy) != (o["x"], o["y"])]
    t.good(t.act(a, "move", to=list(free[0])), "diagonal move")
    after = t.tank(a)
    t.eq(((after["x"], after["y"]), after["ap"]), (free[0], me["ap"] - 1), "move changes tile and costs 1 AP")
    entry = t.view(b)["log"][-1]
    t.eq((entry["event_type"], entry["from_xy"], entry["to_xy"]), ("move", [me["x"], me["y"]], list(free[0])), "move log")


@scenario("actions")
def range_boundary_is_exact(t: T):
    a, b = t.game(2, {"board": {"size": 15}, "start": {"action_range": 3, "hp": 9}})
    for what, body in (("shoot", {}), ("give_ap", {"amount": 1}), ("give_hp", {"amount": 1})):
        t.walk_to_distance(a, b, 4)
        t.err(t.act(a, what, target_id=b.id, **body), 400, "out_of_range", f"{what} at range+1")
        t.walk_to_distance(a, b, 3)
        t.good(t.act(a, what, target_id=b.id, **body), f"{what} at exactly range")


@scenario("actions")
def shoot_rules(t: T):
    a, b, c = t.game(3, {"start": {"ap": 2, "hp": 3}})
    t.err(t.act(a, "shoot", target_id=a.id), 400, "invalid_target", "shoot self")
    t.err(t.act(a, "shoot", target_id="ghost"), 400, "not_found", "shoot unknown tank")
    t.good(t.act(a, "shoot", target_id=b.id), "shoot")
    t.eq((t.tank(a)["ap"], t.tank(a, b)["hp"]), (1, 2), "shot costs 1 AP and deals 1 damage")
    last = t.view(c)["log"][-1]
    t.eq((last["event_type"], last["details"].get("target_hp_after")), ("shoot", 2), "shot log")
    t.good(t.act(a, "shoot", target_id=b.id), "second shot")
    t.err(t.act(a, "shoot", target_id=b.id), 400, "insufficient_ap", "shoot with 0 AP")
    t.eq(t.tank(a, b)["hp"], 1, "rejected shot deals no damage")


@scenario("actions")
def give_ap_rules(t: T):
    a, b = t.game(2, {"start": {"ap": 5}})
    for bad in (0, -1):
        t.err(t.act(a, "give_ap", target_id=b.id, amount=bad), 400, "invalid_amount", f"give {bad} AP")
    for bad in (1.5, "1", True, None):
        t.err(t.act(a, "give_ap", target_id=b.id, amount=bad), 400, "bad_request", f"give {bad!r} AP")
    t.err(t.act(a, "give_ap", target_id=b.id, amount=6), 400, "insufficient_ap", "give more AP than held")
    t.err(t.act(a, "give_ap", target_id=b.id, amount=10**40), 400, "insufficient_ap", "give an enormous amount")
    t.err(t.act(a, "give_ap", target_id=a.id, amount=1), 400, "invalid_target", "give AP to self")
    t.good(t.act(a, "give_ap", target_id=b.id, amount=5), "give all AP")
    t.eq((t.tank(a)["ap"], t.tank(b)["ap"]), (0, 10), "AP moves between tanks")


@scenario("actions")
def give_hp_rules(t: T):
    a, b = t.game(2, {"start": {"hp": 3}})
    t.err(t.act(a, "give_hp", target_id=b.id, amount=3), 400, "insufficient_hp", "give all HP")
    t.err(t.act(a, "give_hp", target_id=b.id, amount=0), 400, "invalid_amount", "give 0 HP")
    t.err(t.act(a, "give_hp", target_id=a.id, amount=1), 400, "invalid_target", "give HP to self")
    ap = t.tank(a)["ap"]
    t.good(t.act(a, "give_hp", target_id=b.id, amount=2), "give down to 1 HP")
    t.eq((t.tank(a)["hp"], t.tank(b)["hp"], t.tank(a)["ap"]), (1, 5, ap), "HP moves and costs no AP")
    t.err(t.act(a, "give_hp", target_id=b.id, amount=1), 400, "insufficient_hp", "give at 1 HP")
    off = t.game(2, {"actions": {"allow_give_hp": False}})
    t.err(t.act(off[0], "give_hp", target_id=off[1].id, amount=1), 400, "disabled", "give HP when disabled")


@scenario("actions")
def upgrade_rules(t: T):
    a, _ = t.game(2, {"start": {"ap": 7, "action_range": 2}, "actions": {"range_upgrade_cost": 3, "max_range": 4}})
    t.good(t.act(a, "upgrade"), "upgrade")
    t.eq((t.tank(a)["action_range"], t.tank(a)["ap"]), (3, 4), "upgrade adds range and costs AP")
    t.good(t.act(a, "upgrade"), "upgrade to max")
    t.err(t.act(a, "upgrade"), 400, "max_range", "upgrade past max")
    t.eq(t.tank(a)["ap"], 1, "rejected upgrade costs nothing")
    b, _ = t.game(2, {"start": {"ap": 2}})
    t.err(t.act(b, "upgrade"), 400, "insufficient_ap", "upgrade without enough AP")


@scenario("actions")
def ap_cap_applies_to_every_gain(t: T):
    a, b, c = t.game(3, {"start": {"ap": 5, "hp": 1}, "ap": {"cap": 6}})
    t.good(t.act(a, "give_ap", target_id=b.id, amount=3), "gift into the cap")
    t.eq((t.tank(a)["ap"], t.tank(b)["ap"]), (2, 6), "giver pays in full and excess is lost")
    t.eq(t.view(b)["log"][-1]["details"].get("received"), 1, "gift log records what arrived")
    t.good(t.act(b, "shoot", target_id=c.id), "kill for loot")
    t.eq(t.tank(b)["ap"], 6, "loot respects the cap")


# == death and winning ===================================================================

@scenario("endgame")
def kill_loots_and_dead_cannot_act(t: T):
    a, b, c = t.game(3, {"start": {"hp": 1, "ap": 4}})
    t.good(t.act(a, "shoot", target_id=b.id), "kill")
    dead = t.tank(a, b)
    t.eq((dead["alive"], dead["hp"], dead["ap"] if "ap" in dead else None), (False, 0, 0), "dead tank state")
    t.eq(t.tank(a)["ap"], 7, "killer gets all the victim's AP")
    kinds = [e["event_type"] for e in t.view(c)["log"]][-3:]
    t.eq(kinds, ["shoot", "kill", "loot"], "kill log order")
    for what, body in (("shoot", {"target_id": a.id}), ("move", {"to": [0, 0]}), ("upgrade", {}),
                       ("give_ap", {"target_id": a.id, "amount": 1}), ("give_hp", {"target_id": a.id, "amount": 1})):
        t.err(t.act(b, what, **body), 400, "dead", f"dead tank tries {what}")
    for what, body in (("shoot", {}), ("give_ap", {"amount": 1}), ("give_hp", {"amount": 1})):
        t.err(t.act(a, what, target_id=b.id, **body), 400, "invalid_target", f"{what} at a dead tank")
    t.eq(t.view(b)["game"]["status"], "active", "dead player can still watch")


@scenario("endgame")
def loot_can_be_disabled(t: T):
    a, b, _ = t.game(3, {"start": {"hp": 1, "ap": 4}, "actions": {"loot_on_kill": False}})
    t.good(t.act(a, "shoot", target_id=b.id), "kill")
    t.eq(t.tank(a)["ap"], 3, "no loot when disabled")
    t.ok(not any(e["event_type"] == "loot" for e in t.view(a)["log"]), "no loot entry")


@scenario("endgame")
def wreck_blocks_only_when_enabled(t: T):
    for blocks in (False, True):
        a, b, _ = t.game(3, {"board": {"size": 12, "wreckage_blocks_tile": blocks}, "start": {"hp": 1}})
        t.walk_to_distance(a, b, 1)
        wreck = t.tank(a, b)
        t.good(t.act(a, "shoot", target_id=b.id), "kill")
        r = t.act(a, "move", to=[wreck["x"], wreck["y"]])
        if blocks:
            t.err(r, 400, "occupied", "move onto a blocking wreck")
        else:
            t.ok(r.status_code == 200, f"move onto a non-blocking wreck: {r.text[:120]}")


@scenario("endgame")
def last_tank_wins_and_game_freezes(t: T):
    a, b = t.game(2, {"start": {"hp": 1}})
    t.good(t.act(a, "shoot", target_id=b.id), "final shot")
    for s in (a, b):
        g = t.view(s)["game"]
        t.eq((g["status"], g["winner_ids"], g["end_reason"]), ("finished", [a.id], "last_standing"), "game over")
    t.eq(t.view(a)["log"][-1]["event_type"], "game_over", "game_over is the last log entry")
    t.err(t.act(a, "upgrade"), 400, "not_active", "act after game over")
    t.err(t.post(a, "start"), 400, "not_lobby", "start after game over")
    t.err(t.post(a, "remove", {"player_id": b.id}), 400, "not_active", "remove after game over")
    t.good(t.post(a, "chat", {"text": "gg"}), "chat after game over")
    t.ok(any(m["text"] == "gg" for m in t.view(b)["messages"]), "finished game keeps chat")


@scenario("endgame")
def host_removes_players_mid_game(t: T):
    h, b, c = t.game(3, {"start": {"ap": 5}})
    t.err(t.post(b, "remove", {"player_id": c.id}), 400, "forbidden", "non-host remove mid-game")
    ap = t.tank(h)["ap"]
    t.good(t.post(h, "remove", {"player_id": b.id}), "remove mid-game")
    gone = t.tank(h, b)
    t.eq((gone["alive"], gone["hp"], gone["ap"]), (False, 0, 0), "removed tank destroyed and AP zeroed")
    t.eq(t.tank(h)["ap"], ap, "removal gives no loot")
    t.eq(t.view(b)["viewer_id"], b.id, "removed player can still watch")
    t.err(t.post(h, "remove", {"player_id": b.id}), 400, "invalid_target", "remove twice")
    t.good(t.post(h, "remove", {"player_id": c.id}), "remove second-to-last")
    g = t.view(h)["game"]
    t.eq((g["status"], g["winner_ids"]), ("finished", [h.id]), "removal can end the game")


@scenario("endgame")
def dead_host_keeps_host_powers(t: T):
    h, b, c = t.game(3, {"start": {"hp": 1}})
    t.good(t.act(b, "shoot", target_id=h.id), "kill the host")
    t.good(t.post(h, "remove", {"player_id": c.id}), "dead host removes a player")
    t.eq(t.view(b)["game"]["winner_ids"], [b.id], "that removal ends the game")


@scenario("endgame", "timed")
def time_limit_tiebreaks(t: T):
    def run(mutate, want):
        cfg = merge(QUIET, {"max_duration_seconds": 3, "start": {"hp": 3, "ap": 4, "action_range": 200}})
        s = t.lobby(3, cfg)
        t.good(t.post(s[0], "start"), "start")
        mutate(s)
        g = t.wait(lambda: (x := t.view(s[0])["game"])["status"] == "finished" and x, 10, "time limit")
        t.eq((g["end_reason"], set(g["winner_ids"])), ("time_limit", {s[i].id for i in want}), "time limit winner")
        t.err(t.act(s[0], "upgrade"), 400, "not_active", "act after time limit")

    def hp(s):  # P1 holds the most HP
        t.good(t.act(s[0], "give_hp", target_id=s[1].id, amount=1), "gift")

    def ap(s):  # all HP equal, P2 holds the most AP
        t.good(t.act(s[0], "give_ap", target_id=s[2].id, amount=1), "gift")

    def reach(s):  # everyone ends on equal HP and 3 AP, but P0 has the most range
        t.good(t.act(s[0], "upgrade"), "upgrade")  # P0: AP 1, range 201
        t.good(t.act(s[1], "give_ap", target_id=s[0].id, amount=1), "level AP")  # P0 2, P1 3
        t.good(t.act(s[2], "give_ap", target_id=s[0].id, amount=1), "level AP")  # P0 3, P2 3

    def ap_beats_range(s):  # equal HP: P0 has the most AP, P1 the most range, and AP is checked first
        t.good(t.act(s[1], "upgrade"), "upgrade")
        t.good(t.act(s[2], "give_ap", target_id=s[0].id, amount=1), "gift")

    def share(_s):  # nothing changes: all three share
        pass

    with ThreadPoolExecutor(5) as ex:
        cases = [(hp, [1]), (ap, [2]), (reach, [0]), (ap_beats_range, [0]), (share, [0, 1, 2])]
        for f in [ex.submit(run, fn, want) for fn, want in cases]:
            f.result()


# == time ================================================================================

@scenario("time", "timed")
def fast_ticks_grant_ap_to_the_living(t: T):
    a, b, c = t.lobby(3, {"debug": {"fast_ticks": True, "tick_seconds": 1}, "start": {"ap": 2, "hp": 1, "action_range": 200}})
    t.good(t.post(a, "start"), "start")
    t.good(t.act(a, "shoot", target_id=b.id), "kill before tick")
    v = t.wait(lambda: (x := t.view(a)) and sum(e["event_type"] == "ap_tick" for e in x["log"]) >= 2 and x, 8, "2 AP ticks")
    ticks = [e for e in v["log"] if e["event_type"] == "ap_tick"]
    t.ok(all(b.id not in e["details"]["recipients"] for e in ticks), "dead tank gets no AP")
    ap_c = next(p for p in v["players"] if p["id"] == c.id)["ap"]
    t.eq(ap_c, 2 + sum(e["amount"] for e in ticks), "AP equals start + ticks")
    deltas = [round(y["ts"] - x["ts"], 3) for x, y in zip(ticks, ticks[1:])]
    t.ok(all(d == 1 for d in deltas), f"ticks are exactly one interval apart: {deltas}")


@scenario("time", "timed")
def ap_cap_holds_during_ticks(t: T):
    a, _ = t.lobby(2, {"debug": {"fast_ticks": True, "tick_seconds": 1}, "ap": {"cap": 2}, "start": {"ap": 1}})
    t.good(t.post(a, "start"), "start")
    t.wait(lambda: sum(e["event_type"] == "ap_tick" for e in t.view(a)["log"]) >= 3, 8, "3 ticks")
    t.eq(t.tank(a)["ap"], 2, "ticks stop at the cap")


@scenario("restart", "timed")
def crash_loses_nothing(t: T):
    srv = t.h.spawn("crash")
    a, b, c = t.game(3, {"fog_of_war": "alliance", "jury": {"enabled": True, "interval_seconds": YEAR},
                         "start": {"hp": 2}}, srv)
    t.good(t.req("POST", f"/api/games/{a.game_id}/chat", a, srv=srv, json={"text": "hello"}), "public chat")
    t.good(t.req("POST", f"/api/games/{a.game_id}/chat", a, srv=srv, json={"to_id": b.id, "text": "psst"}), "DM")
    for body in ({"type": "give_ap", "target_id": b.id, "amount": 2}, {"type": "shoot", "target_id": c.id},
                 {"type": "shoot", "target_id": c.id}):
        t.good(t.req("POST", f"/api/games/{a.game_id}/action", a, srv=srv, json=body), "action")
    t.good(t.req("POST", f"/api/games/{c.game_id}/vote", c, srv=srv, json={"candidate_id": b.id}), "jury vote")
    lobby = t.lobby(2, None, srv)

    def snap(s):
        v = t.view(s, srv)
        v.pop("server_now")
        return v

    before = [snap(s) for s in (a, b, c)]
    srv.crash()
    srv.start()
    for s, want in zip((a, b, c), before):
        got = snap(s)
        t.eq(got, want, f"{s.name}'s full view is identical after a crash")
    t.good(t.req("POST", f"/api/games/{a.game_id}/action", a, srv=srv, json={"type": "upgrade"}), "act after restart")
    t.good(t.req("POST", f"/api/games/{lobby[0].game_id}/start", lobby[0], srv=srv), "lobby survives the crash")
    srv.stop()


@scenario("restart", "timed")
def downtime_catches_up_ap_ticks(t: T):
    srv = t.h.spawn("catchup")
    a, b = t.lobby(2, {"debug": {"fast_ticks": True, "tick_seconds": 1}, "start": {"ap": 0}}, srv)
    t.good(t.req("POST", f"/api/games/{a.game_id}/start", a, srv=srv), "start")
    srv.crash()
    time.sleep(4.2)
    srv.start()
    v = t.wait(lambda: (x := t.view(a, srv)) and next(p for p in x["players"] if p["id"] == a.id)["ap"] >= 4 and x, 5,
               "catch-up AP")
    ticks = sum(e["amount"] for e in v["log"] if e["event_type"] == "ap_tick")
    t.eq(next(p for p in v["players"] if p["id"] == a.id)["ap"], ticks, "AP matches the logged ticks")
    t.eq(next(p for p in t.view(b, srv)["players"] if p["id"] == b.id)["ap"], ticks, "every tank caught up")
    srv.stop()


@scenario("restart", "timed")
def time_limit_passes_during_downtime(t: T):
    srv = t.h.spawn("deadline")
    a, _ = t.lobby(2, merge(QUIET, {"max_duration_seconds": 2}), srv)
    t.good(t.req("POST", f"/api/games/{a.game_id}/start", a, srv=srv), "start")
    srv.crash()
    time.sleep(2.5)
    srv.start()
    g = t.wait(lambda: (x := t.view(a, srv)["game"])["status"] == "finished" and x, 5, "game to end after restart")
    t.eq(g["end_reason"], "time_limit", "time limit applied on catch-up")
    t.ok(abs(g["ended_at"] - (g["started_at"] + 2)) < 0.01, "ended at the deadline, not at restart time")
    srv.stop()


@scenario("restart", "timed")
def finished_game_readable_after_restart(t: T):
    srv = t.h.spawn("archive", ARCHIVE_CACHE_SIZE="0")
    a, b = t.game(2, {"start": {"hp": 1}}, srv)
    t.good(t.req("POST", f"/api/games/{a.game_id}/action", a, srv=srv, json={"type": "shoot", "target_id": b.id}), "win")
    t.good(t.req("POST", f"/api/games/{a.game_id}/chat", b, srv=srv, json={"text": "after the end"}), "chat in archive")
    t.ok(any(m["text"] == "after the end" for m in t.view(a, srv)["messages"]), "archived chat, cache off")
    srv.crash()
    srv.start()
    v = t.view(b, srv)
    t.eq((v["game"]["status"], v["game"]["winner_ids"]), ("finished", [a.id]), "finished game after restart")
    t.ok(any(m["text"] == "after the end" for m in v["messages"]), "chat in finished game after restart")
    srv.stop()


# == fog of war ==========================================================================

@scenario("fog")
def alliance_fog_hides_what_it_should(t: T):
    a, b, c, d = t.game(4, {"fog_of_war": "alliance", "start": {"ap": 5, "hp": 2}})
    t.good(t.act(a, "give_ap", target_id=b.id, amount=1), "secret AP gift")
    t.good(t.act(a, "give_hp", target_id=b.id, amount=1), "secret HP gift")
    t.good(t.act(c, "shoot", target_id=d.id), "shot")
    t.good(t.act(c, "shoot", target_id=d.id), "killing shot")

    def kinds(s):
        return [e["event_type"] for e in t.view(s)["log"]]

    for s in (a, b):
        t.ok({"give_ap", "give_hp"} <= set(kinds(s)), f"{s.name} (a participant) sees the gifts")
    for s in (c, d):
        t.ok(not {"give_ap", "give_hp"} & set(kinds(s)), f"{s.name} must not see other players' gifts")
    t.ok("loot" in kinds(c), "killer sees loot")
    for s in (a, b, d):
        t.ok("loot" not in kinds(s), f"{s.name} must not see the loot")
        t.ok("kill" in kinds(s), f"{s.name} sees the public kill")
    for s in (a, b, c, d):
        v = t.view(s)
        for p in v["players"]:
            t.eq("ap" in p, p["id"] == s.id, f"{s.name} sees AP of {p['name']} only if it is their own")
        for e in v["log"]:
            if e["event_type"] in ("ap_tick", "game_over"):
                t.ok(not {"ap", "balances", "ap_before"} & set(e["details"]), "AP balances in a public log entry")


@scenario("fog")
def fog_stays_on_after_game_over(t: T):
    a, b, c = t.game(3, {"fog_of_war": "alliance", "start": {"hp": 1, "ap": 5}})
    t.good(t.act(a, "give_ap", target_id=b.id, amount=2), "secret gift")
    t.good(t.act(a, "shoot", target_id=c.id), "kill")
    t.good(t.act(a, "shoot", target_id=b.id), "win")
    v = t.view(c)
    t.eq(v["game"]["status"], "finished", "game finished")
    t.ok(not any(e["event_type"] in ("give_ap", "loot") for e in v["log"]), "hidden entries stay hidden after the end")
    t.ok(all("ap" not in p for p in v["players"] if p["id"] != c.id), "other players' AP stays hidden")


@scenario("fog")
def fog_off_shows_everything(t: T):
    a, b, c = t.game(3, {"start": {"hp": 1, "ap": 5}})
    t.good(t.act(a, "give_ap", target_id=b.id, amount=2), "gift")
    t.good(t.act(a, "shoot", target_id=c.id), "kill")
    v = t.view(c)
    t.ok(all("ap" in p for p in v["players"]), "all AP visible")
    t.ok({"give_ap", "loot"} <= {e["event_type"] for e in v["log"]}, "gifts and loot public")


@scenario("fog")
def websocket_pushes_are_redacted_too(t: T):
    a, b, c = t.game(3, {"fog_of_war": "alliance", "start": {"ap": 5}})
    with ws_connect(f"{t.srv.ws_url}/ws/{a.game_id}", open_timeout=5) as ws:
        ws.send(json.dumps({"type": "auth", "token": c.token}))
        first = json.loads(ws.recv(timeout=5))
        t.eq(first["type"], "state", "first push")
        t.good(t.act(a, "give_ap", target_id=b.id, amount=3), "secret gift")
        pushed = json.loads(ws.recv(timeout=5))["state"]
        t.ok(not any(e["event_type"] == "give_ap" for e in pushed["log"]), "gift leaked over WebSocket")
        t.ok(all("ap" not in p for p in pushed["players"] if p["id"] != c.id), "AP leaked over WebSocket")


# == jury ================================================================================

def jury_game(t: T, n: int, **jury) -> list[Seat]:
    return t.game(n, {"jury": {"enabled": True, "interval_seconds": 3, **jury}, "start": {"hp": 1, "ap": 5}})


def awards(t: T, seat: Seat) -> list[dict]:
    return [e for e in t.view(seat)["log"] if e["event_type"] == "jury_award"]


@scenario("jury")
def jury_permissions(t: T):
    off = t.game(3, {"start": {"hp": 1}})
    t.good(t.act(off[0], "shoot", target_id=off[2].id), "kill")
    t.err(t.post(off[2], "vote", {"candidate_id": off[0].id}), 400, "disabled", "vote with jury off")
    a, b, c, d = t.game(4, {"jury": {"enabled": True, "interval_seconds": YEAR}, "start": {"hp": 1, "ap": 5}})
    t.err(t.post(a, "vote", {"candidate_id": b.id}), 400, "forbidden", "living player votes")
    t.good(t.act(a, "shoot", target_id=d.id), "kill")
    t.ok(next(p for p in t.view(a)["players"] if p["id"] == d.id)["is_juror"], "dead player becomes a juror")
    t.err(t.post(d, "vote", {"candidate_id": "ghost"}), 400, "not_found", "vote for unknown player")
    t.err(t.post(d, "vote", {"candidate_id": d.id}), 400, "invalid_target", "juror votes for self (dead)")
    t.good(t.post(d, "vote", {"candidate_id": b.id}), "vote")
    t.good(t.post(d, "vote", {"candidate_id": c.id}), "change vote")
    votes = t.view(a)["jury"]["votes"]
    t.eq(votes, [{"juror_id": d.id, "candidate_id": c.id}], "re-voting replaces the vote")


@scenario("jury", "timed")
def jury_awards_winner(t: T):
    a, b, c, d = jury_game(t, 4)
    t.good(t.act(a, "shoot", target_id=d.id), "kill")
    t.good(t.post(d, "vote", {"candidate_id": b.id}), "vote")
    before = t.tank(b)["ap"]
    got = t.wait(lambda: awards(t, a), 6, "jury award")
    t.eq((got[0]["details"]["winners"], got[0]["details"].get("tally")), ([b.id], {b.id: 1}), "award")
    t.eq(t.tank(b)["ap"], before + 1, "winner gets +1 AP")
    t.eq(t.view(a)["jury"]["votes"], [], "votes reset for the next cycle")


@scenario("jury", "timed")
def jury_tie_rules(t: T):
    for rule, count in (("all", 2), ("random", 1)):
        a, b, c, d, e = jury_game(t, 5, tie_rule=rule)
        t.good(t.act(a, "shoot", target_id=d.id), "kill")
        t.good(t.act(a, "shoot", target_id=e.id), "kill")
        t.good(t.post(d, "vote", {"candidate_id": b.id}), "vote")
        t.good(t.post(e, "vote", {"candidate_id": c.id}), "vote")
        got = t.wait(lambda: awards(t, a), 6, "jury award")
        t.eq(len(got[0]["details"]["winners"]), count, f"tie rule {rule}")


@scenario("jury", "timed")
def vote_for_tank_that_dies_is_void(t: T):
    a, b, c, d = jury_game(t, 4)
    t.good(t.act(a, "shoot", target_id=d.id), "kill")
    t.good(t.post(d, "vote", {"candidate_id": b.id}), "vote")
    t.good(t.act(a, "shoot", target_id=b.id), "candidate dies")
    t.wait(lambda: t.view(a)["jury"]["cycle_index"] >= 1, 6, "cycle to close")
    t.eq(awards(t, a), [], "vote for a dead tank earns nothing")


@scenario("jury", "timed")
def secret_votes_stay_secret(t: T):
    a, b, c, d = jury_game(t, 4, votes_public=False)
    t.good(t.act(a, "shoot", target_id=d.id), "kill")
    t.good(t.post(d, "vote", {"candidate_id": b.id}), "secret vote")
    t.eq(t.view(a)["jury"]["votes"], [], "others can't see a secret vote")
    t.eq(len(t.view(d)["jury"]["votes"]), 1, "juror sees their own vote")
    t.ok(not any(e["event_type"] == "jury_vote" for e in t.view(a)["log"]), "secret vote hidden from log")
    got = t.wait(lambda: awards(t, a), 6, "jury award")
    t.ok("tally" not in got[0]["details"], "tally hidden when votes are secret")


# == chat ================================================================================

@scenario("chat")
def chat_validation(t: T):
    a, b = t.game(2)
    chat = lambda body: t.post(a, "chat", body)
    for bad in ("", "   ", "\n\t"):
        t.err(chat({"text": bad}), 400, "bad_request", f"chat {bad!r}")
    t.err(chat({"text": "x" * 1001}), 400, "bad_request", "chat over the limit")
    t.good(chat({"text": "x" * 1000}), "chat at the limit")
    t.err(chat({"to_id": a.id, "text": "hi"}), 400, "bad_request", "DM to self")
    t.err(chat({"to_id": "ghost", "text": "hi"}), 400, "bad_request", "DM to unknown")
    t.err(chat({"text": 5}), 400, "bad_request", "numeric chat")
    t.err(chat({}), 400, "bad_request", "missing text")
    for text in ("<img src=x onerror=alert(1)>", "🙂 坦克 مرحبا", "line\nbreak", "null"):
        t.good(chat({"text": text}), f"chat {text!r}")
        t.ok(any(m["text"] == text for m in t.view(b)["messages"]), f"{text!r} arrives unchanged")
    for text in ("nul\u0000byte", "\ud800"):  # hostile unicode must never be a 500
        body = json.dumps({"text": text}).encode()
        r = t.req("POST", f"/api/games/{a.game_id}/chat", a, content=body, headers={"content-type": "application/json"})
        t.ok(r.status_code < 500, f"chat with {text!r} crashed the server: {r.status_code}")


@scenario("chat")
def dm_privacy(t: T):
    a, b, c = t.game(3, {"start": {"hp": 1}})
    t.good(t.post(a, "chat", {"to_id": b.id, "text": "secret-plan"}), "DM")
    t.good(t.post(c, "chat", {"text": "public-note"}), "public")
    seen = lambda s: {m["text"] for m in t.view(s)["messages"]}
    t.ok("secret-plan" in seen(a) and "secret-plan" in seen(b), "both DM participants see it")
    t.ok("secret-plan" not in t.req("GET", f"/api/games/{c.game_id}/state", c).text, "DM leaked to a third player")
    t.ok("public-note" in seen(a), "public chat reaches everyone")
    t.good(t.act(c, "shoot", target_id=b.id), "DM recipient dies")
    t.ok("secret-plan" not in seen(b), "dead players lose their old DMs")
    t.err(t.post(b, "chat", {"to_id": a.id, "text": "x"}), 400, "forbidden", "dead player sends DM")
    t.err(t.post(a, "chat", {"to_id": b.id, "text": "x"}), 400, "forbidden", "DM a dead player")
    t.good(t.post(b, "chat", {"text": "from beyond"}), "dead players keep public chat")


# == protocol and abuse ==================================================================

@scenario("protocol")
def auth_boundaries(t: T):
    a, _ = t.game(2)
    other, _ = t.game(2)
    path = f"/api/games/{a.game_id}/state"
    t.err(t.req("GET", path), 401, "unauthorized", "no token")
    t.err(t.req("GET", path, "garbage"), 401, "unauthorized", "bad token")
    t.err(t.req("GET", path, other.token), 401, "unauthorized", "token from another game")
    t.err(t.req("POST", f"/api/games/{a.game_id}/action", other.token, json={"type": "upgrade"}), 401, "unauthorized",
          "act with another game's token")
    t.err(t.req("GET", path, headers={"Authorization": "Basic abc"}), 401, "unauthorized", "wrong auth scheme")
    t.err(t.req("GET", "/api/games/nosuch"), 404, "not_found", "unknown game")
    t.err(t.req("POST", "/api/games/nosuch/join", json={"name": "A"}), 404, "not_found", "join unknown game")
    t.err(t.req("GET", "/api/games/nosuch/state", a), 401, "unauthorized", "state of unknown game")
    v = t.view(a)
    t.ok("token" not in json.dumps(v["players"]), "views never contain tokens")


@scenario("protocol")
def malformed_requests_get_clean_errors(t: T):
    a, b = t.game(2)
    url = f"/api/games/{a.game_id}/action"
    json_hdr = {"content-type": "application/json"}
    for raw in (b"", b"{", b"null", b"[]", b"5", b'"x"', b"\xff\xfe", b'{"type":"move","to":[1,2]', b"{" * 2000):
        t.err(t.req("POST", url, a, content=raw, headers=dict(json_hdr)), 400, "bad_request", f"body {raw[:12]!r}")
    for body in ({"type": "dance"}, {"type": None}, {"type": 5}, {}, {"type": "move"}, {"type": "move", "to": [1]},
                 {"type": "move", "to": [1, 2, 3]}, {"type": "move", "to": "1,2"}, {"type": "move", "to": [1.0, 2]},
                 {"type": "move", "to": ["1", 2]}, {"type": "move", "to": [True, 2]}, {"type": "shoot"},
                 {"type": "shoot", "target_id": 5}, {"type": "shoot", "target_id": None},
                 {"type": "give_ap", "target_id": b.id}, {"type": "upgrade", "extra": 1},
                 {"type": "shoot", "target_id": b.id, "amount": 9}):
        t.err(t.req("POST", url, a, json=body), 400, "bad_request", f"action {body}")
    t.err(t.req("GET", url, a), 405, "method_not_allowed", "GET on a POST route")
    t.err(t.req("GET", "/api/nope"), 404, "not_found", "unknown API route")
    for path in ("start", "close", "leave"):
        r = t.req("POST", f"/api/games/{a.game_id}/{path}", a, content=b"{", headers=dict(json_hdr))
        t.ok(r.status_code < 500, f"junk body on /{path}: {r.status_code}")
    t.eq(t.tank(a)["ap"], 60, "none of that changed the game")


@scenario("protocol")
def websocket_protocol(t: T):
    a, b = t.game(2)
    uri = f"{t.srv.ws_url}/ws/{a.game_id}"

    def bad_auth(message: str, what: str):
        with ws_connect(uri, open_timeout=5) as ws:
            ws.send(message)
            try:
                got = json.loads(ws.recv(timeout=5))
                t.eq((got.get("type"), got.get("error", {}).get("code")), ("error", "unauthorized"), f"WS {what}")
                ws.recv(timeout=5)
                t.fail(f"WS {what}: socket stayed open")
            except ConnectionClosed as e:
                if e.rcvd is None or e.rcvd.code != 4401:
                    t.fail(f"WS {what}: closed with {e.rcvd.code if e.rcvd else 'no code'}, expected 4401")

    for msg, what in (('{"type":"auth","token":"bad"}', "bad token"), ("not json", "non-JSON"), ("[]", "array"),
                      ("{}", "no token"), ('{"token":123}', "numeric token"), ('{"token":["x"]}', "array token"),
                      ('{"token":null}', "null token")):
        bad_auth(msg, what)

    with ws_connect(uri, open_timeout=5) as w1, ws_connect(uri, open_timeout=5) as w2:
        w1.send(json.dumps({"type": "auth", "token": a.token}))
        w2.send(json.dumps({"type": "auth", "token": b.token}))
        s1, s2 = json.loads(w1.recv(timeout=5)), json.loads(w2.recv(timeout=5))
        t.eq((s1["state"]["viewer_id"], s2["state"]["viewer_id"]), (a.id, b.id), "each socket gets its own view")
        w1.send("ping")
        t.eq(w1.recv(timeout=5), "pong", "ping/pong")
        t.good(t.act(a, "upgrade"), "action")
        got = json.loads(w2.recv(timeout=5))
        t.eq(next(p for p in got["state"]["players"] if p["id"] == a.id)["action_range"], 201, "other sockets get pushes")


@scenario("protocol")
def websocket_for_unknown_game(t: T):
    a, _ = t.game(2)
    with ws_connect(f"{t.srv.ws_url}/ws/nosuch", open_timeout=5) as ws:
        ws.send(json.dumps({"token": a.token}))
        t.eq(json.loads(ws.recv(timeout=5)).get("type"), "error", "a real token on the wrong game is refused")


@scenario("protocol")
def accounts_are_off_without_a_client_id(t: T):
    t.eq(t.req("GET", "/api/auth/config").json().get("google_client_id"), None, "sign-in off by default")
    r = t.req("POST", "/api/auth/google", json={"credential": "x"})
    t.ok(400 <= r.status_code < 500, f"sign-in while disabled: {r.status_code}")
    for method, path in (("GET", "/api/me"), ("GET", "/api/me/games"), ("POST", "/api/me/games/abc/claim")):
        t.err(t.req(method, path), 401, "unauthorized", f"{path} without a session")
    t.err(t.req("POST", "/api/games", "expired-session", json={"name": "A"}), 401, "unauthorized",
          "create with an invalid session")


@scenario("protocol")
def static_client_is_served(t: T):
    home = t.req("GET", "/")
    t.ok(home.status_code == 200 and "<html" in home.text.lower(), "home page")
    for asset in re.findall(r'(?:src|href)="(/?[^"#:]+\.(?:js|css))"', home.text):
        t.eq(t.req("GET", "/" + asset.lstrip("/")).status_code, 200, f"asset {asset}")
    t.eq(t.req("GET", "/openapi.json").status_code, 200, "API schema")


@scenario("protocol", "timed")
def rate_limits_engage(t: T):
    srv = t.h.spawn("limits", ACTION_RATE_PER_SECOND="5", CHAT_RATE_PER_10S="20")
    a, b = t.game(2, None, srv)
    codes = [t.req("POST", f"/api/games/{a.game_id}/action", a, srv=srv, json={"type": "give_ap", "target_id": b.id,
                                                                                 "amount": 1}).status_code
             for _ in range(30)]
    t.ok(429 in codes and 200 in codes, f"action limiter never engaged: {set(codes)}")
    other = [t.req("POST", f"/api/games/{b.game_id}/action", b, srv=srv, json={"type": "upgrade"}).status_code]
    t.eq(other[0], 200, "one player's limit doesn't affect another")
    chats = [t.req("POST", f"/api/games/{a.game_id}/chat", a, srv=srv, json={"text": "x"}).status_code for _ in range(40)]
    t.ok(429 in chats and 201 in chats, f"chat limiter never engaged: {set(chats)}")
    srv.stop()


# == concurrency =========================================================================

def together(n: int, fn: Callable[[int], httpx.Response]) -> list[int]:
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait(10)
        return fn(i).status_code

    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(run, range(n)))


@scenario("concurrency")
def one_ap_cannot_be_spent_twice(t: T):
    for _ in range(4):
        a, b = t.game(2, {"start": {"ap": 1, "hp": 9}})
        codes = together(10, lambda _i: t.act(a, "shoot", target_id=b.id))
        t.eq(codes.count(200), 1, "exactly one shot lands with 1 AP")
        t.eq((t.tank(a)["ap"], t.tank(a, b)["hp"]), (0, 8), "AP and HP after the race")


@scenario("concurrency")
def two_tanks_cannot_share_a_tile(t: T):
    for _ in range(4):
        a, b = t.game(2, {"board": {"size": 12}})
        t.walk_to_distance(a, b, 2)
        v = t.view(a)
        pa = next((p["x"], p["y"]) for p in v["players"] if p["id"] == a.id)
        pb = next((p["x"], p["y"]) for p in v["players"] if p["id"] == b.id)
        tile = next((x, y) for x in range(12) for y in range(12) if dist((x, y), pa) == 1 and dist((x, y), pb) == 1)
        codes = together(2, lambda i: t.act((a, b)[i], "move", to=list(tile)))
        t.eq(sorted(codes), [200, 400], "exactly one tank gets the tile")


@scenario("concurrency")
def mutual_kill_has_one_winner(t: T):
    for _ in range(6):
        a, b = t.game(2, {"start": {"hp": 1, "ap": 1}})
        codes = together(2, lambda i: t.act((a, b)[i], "shoot", target_id=(b, a)[i].id))
        g = t.view(a)["game"]
        t.eq((codes.count(200), g["status"], len(g["winner_ids"])), (1, "finished", 1), "mutual shot")


@scenario("concurrency")
def racing_joins_respect_limits(t: T):
    h, = t.lobby(1, {"players": {"maximum": 5}})
    codes = together(12, lambda i: t.req("POST", f"/api/games/{h.game_id}/join", json={"name": f"R{i}"}))
    t.eq(codes.count(201), 4, "joins stop at the maximum")
    t.eq(len(t.view(h)["players"]), 5, "player count")
    h2, = t.lobby(1)
    codes = together(8, lambda _i: t.req("POST", f"/api/games/{h2.game_id}/join", json={"name": "Same"}))
    t.eq(codes.count(201), 1, "only one player gets a contested name")


@scenario("concurrency")
def start_racing_with_join_and_leave(t: T):
    for _ in range(4):
        h, b = t.lobby(2, merge(RICH, {}))
        ops = [lambda: t.post(h, "start"), lambda: t.post(b, "leave"),
               lambda: t.req("POST", f"/api/games/{h.game_id}/join", json={"name": "Racer"})]
        together(3, lambda i: ops[i]())
        v = t.view(h)
        if v["game"]["status"] == "active":
            t.ok(len(v["players"]) >= 2, "an active game has at least two players")
            t.ok(all(p["x"] >= 0 for p in v["players"]), "every seat in an active game was placed")


# == randomized games checked against an independent model ===============================

FUZZ_CONFIGS = [
    {},
    {"fog_of_war": "alliance"},
    {"board": {"size": 4, "wreckage_blocks_tile": True}},
    {"ap": {"cap": 7}, "start": {"ap": 7}},
    {"actions": {"loot_on_kill": False, "allow_give_hp": False}},
    {"actions": {"range_upgrade_cost": 1, "max_range": 5}, "start": {"action_range": 2}},
    {"fog_of_war": "alliance", "board": {"size": 5}, "ap": {"cap": 12}, "start": {"ap": 12, "action_range": 3}},
    {"start": {"hp": 1, "ap": 3, "action_range": 1}, "board": {"size": 3}},
]


class Model:
    """The written rules, reimplemented, so a change in the game shows up as a disagreement."""

    def __init__(self, view: dict, cfg: dict):
        self.cfg, self.size = view["game"]["config"], view["game"]["board_size"]
        self.status, self.winners = "active", []
        self.p = {x["id"]: {k: x[k] for k in ("x", "y", "hp", "action_range", "alive")} for x in view["players"]}
        for q in self.p.values():
            q["ap"] = self.cfg["start"]["ap"]

    def gain(self, q: dict, n: int) -> None:
        cap = self.cfg["ap"]["cap"]
        q["ap"] = q["ap"] + n if cap is None else min(cap, q["ap"] + n)

    def blocked(self, tile) -> bool:
        wrecks = self.cfg["board"]["wreckage_blocks_tile"]
        return any((q["x"], q["y"]) == tile and (q["alive"] or wrecks) for q in self.p.values())

    def apply(self, actor: str, a: dict) -> str | None:
        """Mutates the model if legal. Returns the expected error code, or None."""
        if self.status != "active":
            return "not_active"
        me = self.p[actor]
        if not me["alive"]:
            return "dead"
        kind = a["type"]
        here = (me["x"], me["y"])
        if kind == "move":
            to = tuple(a["to"])
            if not (0 <= to[0] < self.size and 0 <= to[1] < self.size):
                return "out_of_bounds"
            if dist(here, to) != 1:
                return "not_adjacent"
            if self.blocked(to):
                return "occupied"
            if me["ap"] < 1:
                return "insufficient_ap"
            me["ap"] -= 1
            me["x"], me["y"] = to
            return None
        if kind == "upgrade":
            cost, top = self.cfg["actions"]["range_upgrade_cost"], self.cfg["actions"]["max_range"]
            if top is not None and me["action_range"] >= top:
                return "max_range"
            if me["ap"] < cost:
                return "insufficient_ap"
            me["ap"] -= cost
            me["action_range"] += 1
            return None
        if kind == "give_hp" and not self.cfg["actions"]["allow_give_hp"]:
            return "disabled"
        tgt = self.p.get(a["target_id"])
        if tgt is None:
            return "not_found"
        if a["target_id"] == actor or not tgt["alive"]:
            return "invalid_target"
        n = a.get("amount", 1)
        if kind != "shoot" and n < 1:
            return "invalid_amount"
        if dist(here, (tgt["x"], tgt["y"])) > me["action_range"]:
            return "out_of_range"
        if kind == "give_hp":
            if me["hp"] - n < 1:
                return "insufficient_hp"
            me["hp"] -= n
            tgt["hp"] += n
            return None
        if me["ap"] < n:
            return "insufficient_ap"
        if kind == "give_ap":
            me["ap"] -= n
            self.gain(tgt, n)
            return None
        me["ap"] -= 1
        tgt["hp"] -= 1
        if tgt["hp"] == 0:
            tgt["alive"] = False
            loot, tgt["ap"] = tgt["ap"], 0
            if self.cfg["actions"]["loot_on_kill"]:
                self.gain(me, loot)
            living = [pid for pid, q in self.p.items() if q["alive"]]
            if len(living) == 1:
                self.status, self.winners = "finished", living
        return None


def random_action(rng: random.Random, m: Model, actor: str, ids: list[str]) -> dict:
    me = m.p[actor]
    # Mostly plausible actions, with a steady stream of illegal ones.
    kind = rng.choices(["move", "shoot", "give_ap", "give_hp", "upgrade"], [5, 5, 2, 2, 1])[0]
    if kind == "move":
        d = rng.choice([1, 1, 1, 1, 2, 0])
        return {"type": "move", "to": [me["x"] + rng.randint(-d, d), me["y"] + rng.randint(-d, d)]}
    if kind == "upgrade":
        return {"type": "upgrade"}
    target = rng.choice(ids + ["ghost"] if rng.random() < 0.05 else ids)
    if kind == "shoot":
        return {"type": "shoot", "target_id": target}
    return {"type": kind, "target_id": target, "amount": rng.choice([1, 1, 1, 2, 3, 0, -1, 50])}


def fuzz(t: T, games: int) -> None:
    played = 0
    for g in range(games):
        cfg = FUZZ_CONFIGS[g % len(FUZZ_CONFIGS)]
        rng = random.Random(f"{t.h.seed}:{g}")
        n = rng.randint(2, 6)
        if g % len(FUZZ_CONFIGS) == 7:
            n = rng.randint(2, 4)
        seats = t.game(n, merge({"start": {"ap": 20}}, cfg))
        by_id = {s.id: s for s in seats}
        ids = list(by_id)
        m = Model(t.view(seats[0]), cfg)
        where = f"fuzz game {g} (seed {t.h.seed}, {n} players, config {cfg})"
        for step in range(220):
            actor = rng.choice(ids)
            action = random_action(rng, m, actor, ids)
            want = m.apply(actor, action)
            r = t.post(by_id[actor], "action", action)
            played += 1
            got = None if r.status_code == 200 else (r.json().get("error", {}).get("code") if r.status_code < 500 else "5xx")
            if got != want:
                t.fail(f"{where}, step {step}: {action} by {by_id[actor].name}: model expected {want or 'success'}, "
                       f"server said {got or 'success'} ({r.text[:120]})")
                break
            if step % 5 and m.status == "active":
                continue
            if not check_against_model(t, m, seats, where, step):
                break
            if m.status != "active":
                break
    t.ok(played > 0, "fuzz played nothing")
    t.h.stats["fuzz_actions"] = t.h.stats.get("fuzz_actions", 0) + played


def check_against_model(t: T, m: Model, seats: list[Seat], where: str, step: int) -> bool:
    fog = m.cfg["fog_of_war"] != "off"
    good = True
    for seat in seats:
        v = t.view(seat)
        if v["game"]["status"] != m.status or (m.status == "finished" and v["game"]["winner_ids"] != m.winners):
            t.fail(f"{where}, step {step}: status {v['game']['status']} winners {v['game']['winner_ids']}, "
                   f"model says {m.status} {m.winners}")
            return False
        living = [(p["x"], p["y"]) for p in v["players"] if p["alive"]]
        good &= t.ok(len(living) == len(set(living)), f"{where}, step {step}: two tanks share a tile")
        good &= t.ok(all(0 <= x < m.size and 0 <= y < m.size for x, y in living), f"{where}, step {step}: tank off board")
        ids = [e["id"] for e in v["log"]]
        good &= t.ok(ids == sorted(set(ids)), f"{where}, step {step}: log ids out of order")
        for p in v["players"]:
            q = m.p[p["id"]]
            for k in ("x", "y", "hp", "action_range", "alive"):
                if p[k] != q[k]:
                    t.fail(f"{where}, step {step}: {p['name']}.{k} is {p[k]}, model says {q[k]}")
                    return False
            visible = (not fog) or p["id"] == seat.id
            if ("ap" in p) != visible:
                t.fail(f"{where}, step {step}: AP visibility wrong for {p['name']} as seen by {seat.name}")
                return False
            if visible and p["ap"] != q["ap"]:
                t.fail(f"{where}, step {step}: {p['name']}.ap is {p['ap']}, model says {q['ap']}")
                return False
    return good


@scenario("fuzz")
def random_games_match_the_rules(t: T):
    fuzz(t, t.h.fuzz_games)


# == the real page in a browser ==========================================================

class Browser:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.b = None
        for kw in ({"channel": "msedge"}, {}):
            try:
                self.b = self.pw.chromium.launch(headless=True, **kw)
                break
            except Exception:
                continue
        if self.b is None:
            self.pw.stop()
            raise RuntimeError("no browser: install Edge or run `python -m playwright install chromium`")

    def page(self, t: T, width: int = 1280, height: int = 800):
        ctx = self.b.new_context(viewport={"width": width, "height": height})
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: t.fail(f"JavaScript error in the page: {e}"))
        def console(m):
            if m.type == "error" and "Failed to load resource" not in m.text and "WebSocket" not in m.text:
                t.fail(f"console error: {m.text[:200]}")
        pg.on("console", console)
        pg.on("dialog", lambda d: d.accept())
        pg.set_default_timeout(10_000)
        return pg

    def close(self):
        self.b.close()
        self.pw.stop()


def open_seat(t: T, pg, seat: Seat):
    pg.goto(f"{t.srv.url}/#/g/{seat.game_id}?t={seat.token}")
    pg.wait_for_selector(".game-code")


def no_junk_text(t: T, pg, where: str):
    text = pg.locator("body").inner_text()
    for junk in ("undefined", "[object Object]", "NaN"):
        t.ok(junk not in text, f"{where}: page shows {junk!r}")
    t.ok(not re.search(r"(?<![\w])null(?![\w])", text), f"{where}: page shows 'null'")


def tap_tile(pg, x: int, y: int):
    pg.locator("#sheet-root .sheet-backdrop").wait_for(state="detached")
    pg.locator("svg.board").scroll_into_view_if_needed()
    point = pg.evaluate("""([x, y]) => { const s = document.querySelector('svg.board');
        const p = s.createSVGPoint(); p.x = x + .5; p.y = y + .5;
        const q = p.matrixTransform(s.getScreenCTM()); return [q.x, q.y]; }""", [x, y])
    pg.mouse.click(*point)


def press(pg, name: str):
    pg.locator(".sheet").get_by_role("button", name=name, exact=True).click()
    pg.locator("#sheet-root .sheet-backdrop").wait_for(state="detached")


@scenario("browser", "browser")
def create_join_and_start_through_the_ui(t: T):
    host, guest = t.h.browser.page(t), t.h.browser.page(t)
    try:
        host.goto(t.srv.url)
        host.get_by_placeholder("Your display name").first.fill("Hosty")
        host.get_by_role("button", name="Create game").click()
        host.wait_for_selector(".game-code")
        no_junk_text(t, host, "lobby")
        code = re.search(r"[a-z2-9]{6}", host.locator("button.game-code").inner_text()).group(0)

        guest.goto(t.srv.url)
        guest.get_by_placeholder("Game code").fill(code)
        guest.get_by_role("button", name="Join", exact=True).click()
        guest.get_by_placeholder("Your display name").fill("Guesty")
        guest.get_by_role("button", name="Join game").click()
        guest.wait_for_selector(".game-code")
        host.locator(".plist li", has_text="Guesty").wait_for()

        host.get_by_role("button", name=re.compile("^Start game")).click()
        host.wait_for_selector("svg.board")
        guest.wait_for_selector("svg.board")
        no_junk_text(t, host, "active game (host)")
        no_junk_text(t, guest, "active game (guest)")
        guest.reload()
        guest.wait_for_selector("svg.board")
        t.ok(True, "seat survives a page reload")
    finally:
        host.context.close()
        guest.context.close()


@scenario("browser", "browser")
def play_a_whole_game_in_the_ui(t: T):
    a, b, c = t.game(3, {"board": {"size": 6, "wreckage_blocks_tile": True}, "start": {"hp": 2, "ap": 12, "action_range": 6},
                         "jury": {"enabled": True, "interval_seconds": YEAR}})
    pa, pb = t.h.browser.page(t), t.h.browser.page(t)
    try:
        open_seat(t, pa, a)
        open_seat(t, pb, b)
        pa.wait_for_selector("svg.board")
        pb.wait_for_selector("svg.board")

        def at(seat):
            q = t.tank(a, seat)
            return q["x"], q["y"]

        # move
        ax, ay = at(a)
        blocked = {at(s) for s in (a, b, c)}
        tile = next((ax + dx, ay + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                    if (dx or dy) and 0 <= ax + dx < 6 and 0 <= ay + dy < 6 and (ax + dx, ay + dy) not in blocked)
        tap_tile(pa, *tile)
        press(pa, "Move")
        t.wait(lambda: at(a) == tile, 5, "move from the UI")

        # upgrade
        tap_tile(pa, *tile)
        press(pa, "Upgrade range")
        t.wait(lambda: t.tank(a)["action_range"] == 7, 5, "upgrade from the UI")

        # give AP and HP
        pa.wait_for_timeout(150)
        tap_tile(pa, *at(b))
        pa.get_by_label("AP amount").fill("2")
        press(pa, "Give AP")
        t.wait(lambda: t.tank(b)["ap"] == 14, 5, "AP gift from the UI")
        pa.wait_for_timeout(150)
        tap_tile(pa, *at(b))
        press(pa, "Give HP")
        t.wait(lambda: t.tank(b)["hp"] == 3, 5, "HP gift from the UI")

        # a rejected action shows an error instead of breaking the page
        pa.wait_for_timeout(150)
        tap_tile(pa, *at(b))
        pa.get_by_label("AP amount").fill("999")
        pa.locator(".sheet").get_by_role("button", name="Give AP", exact=True).click()
        pa.wait_for_timeout(300)
        t.eq(t.tank(a)["hp"], 1, "rejected gift changes nothing")
        if pa.locator("#sheet-root .sheet-backdrop").count():
            pa.keyboard.press("Escape")

        # shoot Cat to death, with the other page following live
        for hp_left in (1, 0):
            pa.wait_for_timeout(150)
            tap_tile(pa, *at(c))
            press(pa, "Shoot")
            t.wait(lambda: t.tank(a, c)["hp"] == hp_left, 5, "shot from the UI")
        pb.wait_for_function("() => document.querySelectorAll('svg.board .wreck').length === 1", timeout=5000)
        no_junk_text(t, pa, "after a kill")

        # chat in both directions
        pa.get_by_role("tab", name=re.compile("^Chat")).click()
        pa.locator("textarea").fill("hello from the playtest")
        pa.get_by_role("button", name="Send").click()
        t.wait(lambda: any(m["text"] == "hello from the playtest" for m in t.view(b)["messages"]), 5, "chat from the UI")
        pb.get_by_role("tab", name=re.compile("^Chat")).click()
        pb.locator("text=hello from the playtest").wait_for()

        # finish
        for _ in range(3):
            if t.view(a)["game"]["status"] == "finished":
                break
            t.good(t.act(b, "shoot", target_id=a.id), "finish the game")
        pa.get_by_role("heading", name=re.compile("won")).wait_for()
        pb.get_by_role("heading", name=re.compile("You won")).wait_for()
        no_junk_text(t, pa, "game over (loser)")
        no_junk_text(t, pb, "game over (winner)")
        pa.reload()
        pa.wait_for_selector(".game-code")
        no_junk_text(t, pa, "finished game after reload")
    finally:
        pa.context.close()
        pb.context.close()


@scenario("browser", "browser")
def every_tab_renders_in_every_mode(t: T):
    configs = [{}, {"fog_of_war": "alliance"}, {"jury": {"enabled": True, "interval_seconds": YEAR}}]
    for cfg in configs:
        for width in (1280, 360):
            a, b, c = t.game(3, merge({"start": {"hp": 1}}, cfg))
            t.good(t.act(a, "shoot", target_id=c.id), "kill")
            for seat in (a, c):
                pg = t.h.browser.page(t, width, 720)
                try:
                    open_seat(t, pg, seat)
                    pg.wait_for_selector("svg.board")
                    tabs = pg.get_by_role("tab")
                    t.ok(tabs.count() >= 4, f"expected at least 4 tabs, found {tabs.count()}")
                    for i in range(tabs.count()):
                        label = tabs.nth(i).inner_text()
                        tabs.nth(i).click()
                        pg.wait_for_timeout(40)
                        no_junk_text(t, pg, f"tab {label!r} ({width}px, {cfg or 'defaults'})")
                    if width == 360:
                        over = pg.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                        t.ok(over <= 1, f"page scrolls sideways by {over}px on a phone ({cfg or 'defaults'})")
                finally:
                    pg.context.close()


@scenario("browser", "browser")
def hostile_names_are_not_rendered_as_html(t: T):
    evil = "<img src=x onerror=w=1>"
    r = t.good(t.req("POST", "/api/games", json={"name": evil, "config": RICH}), "create")
    h = Seat(r.json(), evil)
    b = Seat(t.good(t.req("POST", f"/api/games/{h.game_id}/join", json={"name": "Bob"}), "join").json(), "Bob")
    t.good(t.post(b, "chat", {"text": "<script>window.w=2</script><b onmouseover=w=3>x</b>"}), "chat")
    t.good(t.post(h, "start"), "start")
    pg = t.h.browser.page(t)
    try:
        open_seat(t, pg, b)
        pg.wait_for_selector("svg.board")
        pg.get_by_role("tab", name=re.compile("^Chat")).click()
        pg.locator("text=<script>window.w=2</script>").first.wait_for()  # shown literally, as text
        t.ok(pg.evaluate("window.w === undefined"), "name or chat text ran as code in the page")
        t.eq(pg.locator("#app img[src='x']").count(), 0, "name was rendered as an HTML element")
    finally:
        pg.context.close()


@scenario("browser", "browser", "timed")
def client_survives_a_server_crash(t: T):
    srv = t.h.spawn("uicrash")
    saved, t.srv = t.srv, srv
    pg = None
    try:
        a, b = t.game(2)
        pg = t.h.browser.page(t)
        open_seat(t, pg, a)
        pg.wait_for_selector("svg.board")
        srv.crash()
        pg.locator("#topbar-info .badge", has_text="reconnecting").wait_for(timeout=10_000)
        srv.start()
        pg.locator("#topbar-info .badge", has_text="reconnecting").wait_for(state="detached", timeout=25_000)
        t.good(t.act(b, "upgrade"), "act after the restart")
        pg.wait_for_function("() => document.body.innerHTML.includes('R201')", timeout=8000)
    finally:
        if pg:
            pg.context.close()
        t.srv = saved
        srv.stop()


# == runner ==============================================================================

class Harness:
    def __init__(self, args):
        self.seed = args.seed if args.seed is not None else random.randrange(1_000_000)
        self.fuzz_games = args.fuzz_games if args.fuzz_games is not None else (8 if args.quick else 32)
        self.work = Path(tempfile.mkdtemp(prefix="tt-playtest-"))
        self.main = Server(self.work, "main")
        self.extra: list[Server] = []
        self.browser: Browser | None = None
        self.stats: dict[str, int] = {}

    def spawn(self, name: str, **env) -> Server:
        srv = Server(self.work, name, **env)
        self.extra.append(srv)
        srv.start()
        return srv

    def cleanup(self) -> None:
        for s in [self.main, *self.extra]:
            s.stop()
        if self.browser:
            self.browser.close()
        time.sleep(0.2)
        shutil.rmtree(self.work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--quick", action="store_true", help="skip the browser, shorter fuzz")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--only", help="comma-separated areas or scenario names")
    ap.add_argument("--seed", type=int, help="fuzz seed, printed on every run")
    ap.add_argument("--fuzz-games", type=int)
    ap.add_argument("--out", help="write a JSON report here")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for area, name, _fn, _tags in SCENARIOS:
            print(f"{area:<12} {name}")
        print(f"\n{len(SCENARIOS)} scenarios")
        return 0

    wanted = {w.strip() for w in args.only.split(",")} if args.only else None
    chosen = [s for s in SCENARIOS if wanted is None or s[0] in wanted or s[1] in wanted]
    if wanted and not chosen:
        print(f"No scenario matches {args.only!r}. Use --list.")
        return 2

    h = Harness(args)
    results: list[dict] = []
    skipped: list[str] = []
    started = time.monotonic()
    print(f"Tank Tactics playtest, seed {h.seed}")
    try:
        try:
            h.main.start()
        except RuntimeError as e:
            print(f"HARNESS ERROR: {e}")
            return 2
        no_ui = args.quick or args.no_browser
        if not no_ui and any("browser" in s[3] for s in chosen):
            try:
                h.browser = Browser()
            except Exception as e:
                print(f"Browser unavailable, skipping browser scenarios: {e}")
                no_ui = True
        for area, name, fn, tags in chosen:
            if "browser" in tags and no_ui:
                skipped.append(f"{area}/{name}")
                continue
            t = T(h, name)
            before = h.main.tracebacks()
            t0 = time.monotonic()
            try:
                fn(t)
            except Failed:
                pass
            except Exception as e:
                t.fail(f"{type(e).__name__}: {e}\n" + "".join(traceback.format_exc(limit=-3)))
            new_tb = h.main.tracebacks() - before
            if new_tb:
                t.fail(f"server logged {new_tb} unhandled exception(s):\n{h.main.log_tail(12)}")
            dt = time.monotonic() - t0
            results.append({"area": area, "scenario": name, "passed": not t.failures, "checks": t.checks,
                            "seconds": round(dt, 2), "failures": t.failures})
            print(f"  {'PASS' if not t.failures else 'FAIL'}  {area}/{name} ({t.checks} checks, {dt:.1f}s)")
            for f in t.failures[:5]:
                print("        - " + f.replace("\n", "\n          "))
            if len(t.failures) > 5:
                print(f"        ... and {len(t.failures) - 5} more")
            if h.main.proc.poll() is not None:
                print("HARNESS: the main server died; restarting it")
                t.fail("the server process exited during this scenario")
                results[-1].update(passed=False, failures=t.failures)
                h.main.start()
    finally:
        h.cleanup()

    failed = [r for r in results if not r["passed"]]
    checks = sum(r["checks"] for r in results)
    print(f"\n{len(results) - len(failed)}/{len(results)} scenarios passed, {checks} checks, "
          f"{h.stats.get('fuzz_actions', 0)} random actions, {time.monotonic() - started:.0f}s, seed {h.seed}")
    if skipped:
        print(f"SKIPPED {len(skipped)}: " + ", ".join(skipped))
    if args.out:
        Path(args.out).write_text(json.dumps({"seed": h.seed, "passed": not failed, "results": results,
                                              "skipped": skipped, "stats": h.stats}, indent=1), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Game service: owns the in-memory game states, serialises every change per game
behind a mutex, persists changes, and builds per-viewer payloads.

Only live games (lobby/active) are held in memory for good. Finished and closed
games are dropped once they end and read back from the database on demand, with a
small LRU cache, so memory does not grow with the number of games ever played.

Transport-agnostic: the HTTP/WebSocket server (server.py) and the tests both use it.
"""
from __future__ import annotations

import copy
import random
import secrets
import threading
import time
from collections import OrderedDict, defaultdict
from typing import Any, Callable

from . import game, recurrence
from .config import ConfigError, DebugConfig, GameConfig, ServerConfig, is_valid_unicode
from .store import Message, Store, User, hash_token


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class RateLimiter:
    """Token bucket per key."""

    def __init__(self, rate: float, burst: float, clock: Callable[[], float]):
        self.rate, self.burst, self.clock = rate, burst, clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
            now = self.clock()
            tokens, last = self._buckets.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens < 1:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1, now)
            return True


LIVE_STATUSES = ("lobby", "active")


def _new_id(n: int = 6) -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"  # no lookalikes, easy to type
    return "".join(secrets.choice(alphabet) for _ in range(n))



class GameService:
    def __init__(
        self,
        store: Store,
        server_config: ServerConfig | None = None,
        clock: Callable[[], float] = time.time,
        rng: random.Random | None = None,
    ):
        self.store = store
        self.cfg = server_config or ServerConfig()
        self.clock = clock
        self.rng = rng or random.Random()
        # Live games only; see ``get`` for finished/closed ones.
        self.games: dict[str, game.GameState] = {s.id: s for s in store.load_all(LIVE_STATUSES)}
        self.tokens: dict[str, tuple[str, str]] = store.token_index()
        self.messages: dict[str, list[Message]] = {gid: store.load_messages(gid) for gid in self.games}
        # Recently used finished/closed games: id -> (state, messages), least recent first.
        self._archive: OrderedDict[str, tuple[game.GameState, list[Message]]] = OrderedDict()
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._global = threading.Lock()
        self.action_limiter = RateLimiter(self.cfg.action_rate_per_second, self.cfg.action_rate_per_second, clock)
        self.chat_limiter = RateLimiter(self.cfg.chat_rate_per_10s / 10, self.cfg.chat_rate_per_10s, clock)
        self.login_limiter = RateLimiter(self.cfg.login_rate_per_minute / 60, self.cfg.login_rate_per_minute, clock)
        # Schedule previews are typed live (debounced) in the lobby form; per client IP.
        self.preview_limiter = RateLimiter(5, 30, clock)
        # Called with (game_id, new_entries) after every committed change. The API's Hub hooks this.
        self.on_change: Callable[[str, list[game.LogEntry]], None] = lambda gid, entries: None

    # -- helpers ----------------------------------------------------------------

    def _lock(self, game_id: str) -> threading.Lock:
        with self._global:
            return self._locks[game_id]

    def get(self, game_id: str) -> game.GameState | None:
        """The game's current state, live or archived, or None if there is no such game."""
        state = self.games.get(game_id)
        if state is not None:
            return state
        hit = self._archived(game_id)
        return hit[0] if hit else None

    def _game(self, game_id: str) -> game.GameState:
        state = self.get(game_id)
        if state is None:
            raise ServiceError("not_found", "Game not found.", 404)
        return state

    def _messages(self, game_id: str) -> list[Message]:
        msgs = self.messages.get(game_id)
        if msgs is not None:
            return msgs
        hit = self._archived(game_id)
        return hit[1] if hit else []

    def _archived(self, game_id: str) -> tuple[game.GameState, list[Message]] | None:
        """A finished/closed game from the cache, or loaded from the database."""
        with self._global:
            hit = self._archive.get(game_id)
            if hit is not None:
                self._archive.move_to_end(game_id)
                return hit
        state = self.store.load_game(game_id)
        # A live game missing from ``self.games`` is mid-creation; never cache it here.
        if state is None or state.status in LIVE_STATUSES:
            return None
        hit = (state, self.store.load_messages(game_id))
        with self._global:
            hit = self._archive.setdefault(game_id, hit)  # another thread may have loaded it first
            self._remember(game_id)
        return hit

    def _remember(self, game_id: str) -> None:
        """Mark an archived game most recently used and trim the cache. Call under ``_global``."""
        self._archive.move_to_end(game_id)
        while len(self._archive) > self.cfg.archive_cache_size:
            self._archive.popitem(last=False)

    def _put(self, state: game.GameState) -> None:
        """Install a committed state: live games stay resident, ended ones move to the archive."""
        with self._global:
            if state.status in LIVE_STATUSES:
                self.games[state.id] = state
                return
            self.games.pop(state.id, None)
            msgs = self.messages.pop(state.id, None)
            if msgs is None:
                old = self._archive.get(state.id)
                msgs = old[1] if old else self.store.load_messages(state.id)
            self._archive[state.id] = (state, msgs)
            self._remember(state.id)

    def authenticate(self, game_id: str, token: Any) -> str:
        """Return the player id for ``token`` (untrusted, any JSON value) in ``game_id``."""
        if not token:
            raise ServiceError("unauthorized", "Missing player token.", 401)
        if not isinstance(token, str) or not is_valid_unicode(token):
            raise ServiceError("unauthorized", "Invalid player token for this game.", 401)
        hit = self.tokens.get(hash_token(token))
        if hit is None or hit[0] != game_id:
            raise ServiceError("unauthorized", "Invalid player token for this game.", 401)
        return hit[1]

    def _mutate(
        self,
        game_id: str,
        fn: Callable[[game.GameState], list[game.LogEntry]],
        new_tokens: dict[str, str] | None = None,
        owners: dict[str, str] | None = None,
    ) -> list[game.LogEntry]:
        """Run ``fn`` on the game under its mutex and persist the result atomically.

        The game rules validate before mutating, but to be safe against any
        failure (including a DB error), ``fn`` runs on a copy that only replaces
        the live state once the database commit succeeded.
        """
        with self._lock(game_id):
            live = self._game(game_id)
            work = copy.deepcopy(live)
            try:
                entries = fn(work)
            except game.GameError as e:
                raise ServiceError(e.code, e.message) from None
            except ConfigError as e:
                raise ServiceError("invalid_config", str(e)) from None
            self.store.save(work, entries, new_tokens, owners)
            self._put(work)
        self.on_change(game_id, entries)
        return entries

    # -- lobby ------------------------------------------------------------------

    def _check_debug(self, before: DebugConfig, after: DebugConfig) -> None:
        """Without ``allow_debug``, debug options can't be changed except back to the defaults.
        Games that already have them keep them, so a config change can't break a running game."""
        if not self.cfg.allow_debug and after != before and after != DebugConfig():
            raise ConfigError("Debug options are disabled on this server.")

    def create_game(self, host_name: Any, config: Any = None, user_id: str | None = None) -> dict[str, str]:
        """``user_id``: the signed-in account to link the host's seat to, if any."""
        try:
            cfg = GameConfig.parse(config)
            self._check_debug(DebugConfig(), cfg.debug)
        except ConfigError as e:
            raise ServiceError("invalid_config", str(e)) from None
        with self._global:
            game_id = _new_id()
            while game_id in self.games or self.store.has_game(game_id):
                game_id = _new_id()
        player_id, token = secrets.token_hex(8), secrets.token_urlsafe(24)
        try:
            state = game.create_game(game_id, player_id, host_name, cfg, self.clock())
        except game.GameError as e:
            raise ServiceError(e.code, e.message) from None
        th = hash_token(token)
        self.store.save(state, state.log, {player_id: th}, {player_id: user_id} if user_id else None)
        with self._global:
            self.games[game_id] = state
            self.messages[game_id] = []
            self.tokens[th] = (game_id, player_id)
        return {"game_id": game_id, "player_id": player_id, "token": token}

    def join_game(self, game_id: str, name: Any, user_id: str | None = None) -> dict[str, str]:
        """``user_id``: the signed-in account to link the seat to. An account holds at
        most one seat per game (the database enforces it too)."""
        if user_id is not None and self.store.user_seat(game_id, user_id) is not None:
            raise ServiceError("already_joined", "You are already in this game.", 409)
        player_id, token = secrets.token_hex(8), secrets.token_urlsafe(24)
        th = hash_token(token)
        self._mutate(game_id, lambda s: game.add_player(s, player_id, name, self.clock()), {player_id: th},
                     {player_id: user_id} if user_id else None)
        with self._global:
            self.tokens[th] = (game_id, player_id)
        return {"game_id": game_id, "player_id": player_id, "token": token}

    def public_info(self, game_id: str) -> dict[str, Any]:
        """What someone holding just the invite link may see before joining."""
        s = self._game(game_id)
        host = next((p.name for p in s.players if p.is_host), None)
        return {"id": s.id, "status": s.status, "host": host, "player_count": len(s.players),
                "max_players": s.config.players.maximum}

    def update_config(self, game_id: str, player_id: str, patch: Any) -> None:
        def apply(s: game.GameState) -> list[game.LogEntry]:
            before = s.config.debug
            entries = game.update_config(s, player_id, patch, self.clock())
            self._check_debug(before, s.config.debug)  # raising discards _mutate's working copy
            return entries

        self._mutate(game_id, apply)

    def preview_schedule(self, rule: Any, tz: Any, count: int = 5) -> dict[str, Any]:
        """Validate an AP schedule as the lobby would and list its next occurrences if a
        game started now. Lets the client show a live summary without its own RRULE code."""
        field = "timezone"
        try:
            recurrence.check_timezone(tz)
            field = "rrule"
            norm = recurrence.validate(rule, tz)
            occurrences = recurrence.preview(norm, tz, self.clock(), count)
        except recurrence.RecurrenceError as e:
            raise ServiceError("invalid_schedule", f"{field}: {e}") from None
        return {"rrule": norm, "timezone": tz, "summary": recurrence.describe(norm, tz), "occurrences": occurrences}

    def start_game(self, game_id: str, player_id: str) -> None:
        self._mutate(game_id, lambda s: game.start_game(s, player_id, self.clock(), self.rng))

    def remove_player(self, game_id: str, player_id: str, target_id: Any) -> None:
        if not isinstance(target_id, str):
            raise ServiceError("not_found", "No such player.")
        self._mutate(game_id, lambda s: game.remove_player(s, player_id, target_id, self.clock()))
        # A player removed from a lobby is gone; mid-game they stay on as a spectator.
        if game.find_player(self._game(game_id), target_id) is None:
            self._revoke_token(game_id, target_id)

    def leave_game(self, game_id: str, player_id: str) -> None:
        """Leave a lobby (not the host). The player's token stops working."""
        self._mutate(game_id, lambda s: game.leave_lobby(s, player_id, self.clock()))
        self._revoke_token(game_id, player_id)

    def close_game(self, game_id: str, player_id: str) -> None:
        """Host cancels the lobby. Tokens keep working so everyone can see it was closed."""
        self._mutate(game_id, lambda s: game.close_lobby(s, player_id, self.clock()))

    def _revoke_token(self, game_id: str, player_id: str) -> None:
        with self._global:
            for th, (gid, pid) in list(self.tokens.items()):
                if gid == game_id and pid == player_id:
                    del self.tokens[th]

    # -- accounts ---------------------------------------------------------------

    def sign_in(self, google_sub: str, name: str) -> dict[str, Any]:
        """Sign in (creating the account on first use) after the API has verified a
        Google ID token. Returns a new session token, shown to the client once."""
        name = " ".join(name.split())[:64] or "Player"
        now = self.clock()
        user = self.store.upsert_google_user(google_sub, name, secrets.token_hex(8), now)
        token = secrets.token_urlsafe(32)
        self.store.add_auth_session(hash_token(token), user.id, now, now + self.cfg.session_ttl_days * 86_400)
        return {"token": token, "user": {"id": user.id, "name": user.name}}

    def session_user(self, token: str | None) -> User:
        """The user a session token belongs to (401 if missing, unknown or expired)."""
        if not token:
            raise ServiceError("unauthorized", "Sign in first.", 401)
        user = self.store.session_user(hash_token(token), self.clock())
        if user is None:
            raise ServiceError("unauthorized", "Your sign-in has expired. Sign in again.", 401)
        return user

    def sign_out(self, token: str) -> None:
        self.store.delete_auth_session(hash_token(token))

    def user_games(self, user_id: str) -> list[dict[str, Any]]:
        """The seats an account holds, newest game first."""
        return [
            {"game_id": s.game_id, "player_id": s.player_id, "name": s.name, "status": s.status, "is_host": s.is_host}
            for s in self.store.user_seats(user_id)
        ]

    def claim_seat(self, game_id: str, user_id: str) -> dict[str, str]:
        """A fresh token for the account's seat in this game, so it can be played from
        another device. Earlier tokens for the seat keep working."""
        player_id = self.store.user_seat(game_id, user_id)
        if player_id is None:
            raise ServiceError("not_found", "You have no seat in this game.", 404)
        token = secrets.token_urlsafe(24)
        th = hash_token(token)
        with self._lock(game_id):
            # Under the game lock, so a concurrent removal can't leave a dangling token.
            if game.find_player(self._game(game_id), player_id) is None:
                raise ServiceError("not_found", "You have no seat in this game.", 404)
            self.store.add_seat_token(th, game_id, player_id, self.clock())
            with self._global:
                self.tokens[th] = (game_id, player_id)
        return {"game_id": game_id, "player_id": player_id, "token": token}

    # -- game actions -----------------------------------------------------------

    def act(self, game_id: str, player_id: str, action: game.Action | dict[str, Any]) -> list[game.LogEntry]:
        """Apply a player action: a parsed command, or a raw payload to validate."""
        try:
            command = game.parse_action(action)
        except game.GameError as e:
            raise ServiceError(e.code, e.message) from None
        if not self.action_limiter.allow(player_id):
            raise ServiceError("rate_limited", "Slow down: too many actions.", 429)

        def run(s: game.GameState) -> list[game.LogEntry]:
            # Any AP tick that is due happens before the action, so the player sees fresh AP.
            now = self.clock()
            return game.advance_time(s, now, self.rng) + command.apply(s, player_id, now)

        return self._mutate(game_id, run)

    def vote(self, game_id: str, player_id: str, candidate_id: Any) -> None:
        if not self.action_limiter.allow(player_id):
            raise ServiceError("rate_limited", "Slow down: too many actions.", 429)

        def run(s: game.GameState) -> list[game.LogEntry]:
            now = self.clock()
            return game.advance_time(s, now, self.rng) + game.cast_jury_vote(s, player_id, candidate_id, now)

        self._mutate(game_id, run)

    # -- time -------------------------------------------------------------------

    def tick(self) -> None:
        """Apply any due AP ticks / jury closes / time limits for every active game."""
        now = self.clock()
        for game_id, state in list(self.games.items()):
            due = game.next_due_at(state)
            if due is not None and due <= now:
                self._mutate(game_id, lambda s: game.advance_time(s, self.clock(), self.rng))

    def seconds_until_next_due(self) -> float | None:
        dues = [d for d in (game.next_due_at(s) for s in self.games.values()) if d is not None]
        return max(0.0, min(dues) - self.clock()) if dues else None

    # -- chat -------------------------------------------------------------------

    def send_message(self, game_id: str, player_id: str, to_id: Any, text: Any) -> Message:
        if not isinstance(text, str) or not text.strip():
            raise ServiceError("bad_request", "Message is empty.")
        text = text.strip()
        if len(text) > self.cfg.max_message_length:
            raise ServiceError("bad_request", f"Message is too long (max {self.cfg.max_message_length}).")
        if not self.chat_limiter.allow(player_id):
            raise ServiceError("rate_limited", "Slow down: too many messages.", 429)
        with self._lock(game_id):
            s = self._game(game_id)
            sender = game.get_player(s, player_id)
            if to_id is not None:
                rcpt = game.find_player(s, to_id) if isinstance(to_id, str) else None
                if rcpt is None or rcpt.id == sender.id:
                    raise ServiceError("bad_request", "No such recipient.")
                # Dead players keep access to the public channel only.
                if not sender.alive:
                    raise ServiceError("forbidden", "Destroyed tanks can only use the public channel.")
                if not rcpt.alive:
                    raise ServiceError("forbidden", "That player's tank is destroyed; they can only read public chat.")
            msg = self.store.add_message(game_id, self.clock(), player_id, to_id, text)
            self._messages(game_id).append(msg)
        self.on_change(game_id, [])
        return msg

    def visible_messages(self, game_id: str, viewer_id: str | None) -> list[dict[str, Any]]:
        s = self._game(game_id)
        viewer = game.find_player(s, viewer_id) if viewer_id else None
        sees_dms = viewer is not None and viewer.alive
        out = []
        for m in self._messages(game_id):
            if m.to_id is None or (sees_dms and viewer is not None and viewer.id in (m.from_id, m.to_id)):
                out.append(m.to_dict())
        return out

    # -- views ------------------------------------------------------------------

    def view(self, game_id: str, viewer_id: str | None) -> dict[str, Any]:
        """The full payload for one viewer. Everything sent to a client goes through here."""
        s = self._game(game_id)
        v = game.view_for(s, viewer_id)
        v["messages"] = self.visible_messages(game_id, viewer_id)
        v["server_now"] = self.clock()
        v["allow_debug"] = self.cfg.allow_debug
        return v

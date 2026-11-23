"""SQLite persistence via SQLAlchemy (2.0-style ORM).

The server keeps game states in memory and writes every change through to the
database in one transaction. On boot it loads the live games (lobby/active) back,
and the scheduler then catches up any missed ticks; finished and closed games stay
on disk and are read on demand.
"""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import JSON, ForeignKey, Index, create_engine, delete, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import GameConfig
from .game import EventType, GameState, JuryVote, LogEntry, Player


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Message:
    id: int
    game_id: str
    ts: float
    from_id: str
    to_id: str | None  # None = public channel
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ts": self.ts, "from_id": self.from_id, "to_id": self.to_id, "text": self.text}


# -- schema ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    # none_as_null: store Python None as SQL NULL, not the JSON text 'null'.
    type_annotation_map = {dict[str, Any]: JSON(none_as_null=True), list[Any]: JSON(none_as_null=True)}


class GameRow(Base):
    __tablename__ = "games"

    id: Mapped[str] = mapped_column(primary_key=True)
    status: Mapped[str]
    config: Mapped[dict[str, Any]]
    created_at: Mapped[float]
    started_at: Mapped[float | None]
    ended_at: Mapped[float | None]
    winner_ids: Mapped[list[Any]] = mapped_column(default=list)
    end_reason: Mapped[str | None]
    board_size: Mapped[int]
    last_ap_tick_at: Mapped[float | None]
    last_jury_cycle_at: Mapped[float | None]
    jury_cycle_index: Mapped[int] = mapped_column(default=0)
    next_log_id: Mapped[int] = mapped_column(default=1)

    # Players removed from a lobby disappear from this list and are deleted.
    players: Mapped[list[PlayerRow]] = relationship(
        order_by="PlayerRow.seq", cascade="all, delete-orphan", lazy="selectin"
    )


class PlayerRow(Base):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(primary_key=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    seq: Mapped[int]  # join order
    name: Mapped[str]
    color: Mapped[str]
    x: Mapped[int]
    y: Mapped[int]
    hp: Mapped[int]
    ap: Mapped[int]
    action_range: Mapped[int]
    alive: Mapped[bool]
    is_host: Mapped[bool]
    died_at: Mapped[float | None]
    joined_jury_at: Mapped[float | None]
    token_hash: Mapped[str] = mapped_column(unique=True)  # SHA-256 of the player's secret token
    # The signed-in account that owns this seat, if any. Never part of a game view.
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)


class SeatTokenRow(Base):
    """Extra tokens for a seat, issued when a signed-in user opens it on another device.
    Deleted with the player (removed from a lobby) by the database's ON DELETE CASCADE."""

    __tablename__ = "seat_tokens"

    token_hash: Mapped[str] = mapped_column(primary_key=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), index=True)
    game_id: Mapped[str]
    created_at: Mapped[float]


class UserRow(Base):
    """An account from "Sign in with Google". Only the stable Google subject id and a
    display name are kept; no email address or other profile data is stored."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(primary_key=True)
    google_sub: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    created_at: Mapped[float]


class AuthSessionRow(Base):
    __tablename__ = "auth_sessions"

    token_hash: Mapped[str] = mapped_column(primary_key=True)  # SHA-256 of the session token
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[float]
    expires_at: Mapped[float]


@dataclass
class User:
    id: str
    name: str


@dataclass
class Seat:
    """A user's seat in one game, for the "your games" list."""

    game_id: str
    player_id: str
    name: str
    status: str
    is_host: bool
    created_at: float


class ActionRow(Base):
    """The immutable action log. Rows are only ever inserted."""

    __tablename__ = "actions"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), primary_key=True)
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[float]
    actor_id: Mapped[str | None]
    event_type: Mapped[str]
    target_id: Mapped[str | None]
    amount: Mapped[int | None]
    from_xy: Mapped[list[Any] | None]
    to_xy: Mapped[list[Any] | None]
    details: Mapped[dict[str, Any]]
    visibility: Mapped[str]


class JuryVoteRow(Base):
    __tablename__ = "jury_votes"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), primary_key=True)
    cycle_index: Mapped[int] = mapped_column(primary_key=True)
    juror_id: Mapped[str] = mapped_column(primary_key=True)
    candidate_id: Mapped[str]
    ts: Mapped[float]


class MessageRow(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("messages_game", "game_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"))
    ts: Mapped[float]
    from_id: Mapped[str]
    to_id: Mapped[str | None]
    text: Mapped[str]


# -- store ----------------------------------------------------------------------------

def _make_engine(path: str) -> Engine:
    if path == ":memory:":
        # One shared connection, or each session would see its own empty database.
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    else:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        engine = create_engine(f"sqlite:///{os.path.abspath(path)}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


class Store:
    def __init__(self, path: str):
        self._engine = _make_engine(path)
        Base.metadata.create_all(self._engine)
        _migrate(self._engine)
        self._sessions = sessionmaker(self._engine)
        # SQLite allows one writer; serialising here also makes the in-memory
        # database (a single shared connection) safe to use from several threads.
        self._lock = threading.Lock()

    def close(self) -> None:
        self._engine.dispose()

    # -- writes ---------------------------------------------------------------

    def save(
        self,
        state: GameState,
        new_entries: Iterable[LogEntry] = (),
        new_tokens: dict[str, str] | None = None,
        owners: dict[str, str] | None = None,
    ) -> None:
        """Write the game, all players, new log entries and the current jury votes in
        one transaction. ``new_tokens`` maps player id -> token hash for new players,
        and ``owners`` player id -> user id for new players who are signed in."""
        new_tokens, owners = new_tokens or {}, owners or {}
        with self._lock, self._sessions.begin() as session:
            game = session.get(GameRow, state.id)
            if game is None:
                game = GameRow(id=state.id, created_at=state.created_at)
                session.add(game)
            game.status = state.status
            game.config = state.config.model_dump(mode="json")
            game.started_at = state.started_at
            game.ended_at = state.ended_at
            game.winner_ids = list(state.winner_ids)
            game.end_reason = state.end_reason
            game.board_size = state.board_size
            game.last_ap_tick_at = state.last_ap_tick_at
            game.last_jury_cycle_at = state.last_jury_cycle_at
            game.jury_cycle_index = state.jury_cycle_index
            game.next_log_id = state.next_log_id

            existing = {row.id: row for row in game.players}
            rows = []
            for seq, p in enumerate(state.players):
                row = existing.get(p.id) or PlayerRow(id=p.id, token_hash=new_tokens[p.id], user_id=owners.get(p.id))
                _copy_player(p, row, seq)
                rows.append(row)
            game.players = rows  # anything no longer listed is deleted (delete-orphan)
            # Actions and votes reference the game by foreign key but have no ORM
            # relationship to it, so make sure the game row exists before writing them.
            session.flush()

            session.execute(delete(JuryVoteRow).where(JuryVoteRow.game_id == state.id))
            session.add_all(_action_row(state.id, e) for e in new_entries)
            session.add_all(
                JuryVoteRow(game_id=state.id, cycle_index=v.cycle_index, juror_id=v.juror_id,
                            candidate_id=v.candidate_id, ts=v.ts)
                for v in state.jury_votes
            )

    def add_message(self, game_id: str, ts: float, from_id: str, to_id: str | None, text: str) -> Message:
        with self._lock, self._sessions.begin() as session:
            row = MessageRow(game_id=game_id, ts=ts, from_id=from_id, to_id=to_id, text=text)
            session.add(row)
            session.flush()  # assigns the id
            return _message(row)

    # -- reads ----------------------------------------------------------------

    def load_game(self, game_id: str) -> GameState | None:
        with self._lock, self._sessions() as session:
            game = session.get(GameRow, game_id)
            return None if game is None else _hydrate(session, game)

    def load_all(self, statuses: Iterable[str] | None = None) -> list[GameState]:
        """Every game, or only those whose status is in ``statuses``."""
        q = select(GameRow)
        if statuses is not None:
            q = q.where(GameRow.status.in_(list(statuses)))
        with self._lock, self._sessions() as session:
            return [_hydrate(session, g) for g in session.scalars(q)]

    def has_game(self, game_id: str) -> bool:
        with self._lock, self._sessions() as session:
            return session.get(GameRow, game_id) is not None

    def token_index(self) -> dict[str, tuple[str, str]]:
        """token hash -> (game id, player id), for both original and extra seat tokens."""
        with self._lock, self._sessions() as session:
            rows = session.execute(select(PlayerRow.token_hash, PlayerRow.game_id, PlayerRow.id))
            extra = session.execute(select(SeatTokenRow.token_hash, SeatTokenRow.game_id, SeatTokenRow.player_id))
            return {th: (gid, pid) for th, gid, pid in [*rows, *extra]}

    # -- accounts ---------------------------------------------------------------

    def upsert_google_user(self, sub: str, name: str, new_id: str, now: float) -> User:
        """The user for this Google subject, created on first sign-in. The stored name
        follows the Google profile name."""
        with self._lock, self._sessions.begin() as session:
            row = session.scalars(select(UserRow).where(UserRow.google_sub == sub)).one_or_none()
            if row is None:
                row = UserRow(id=new_id, google_sub=sub, name=name, created_at=now)
                session.add(row)
            else:
                row.name = name
            return User(row.id, row.name)

    def add_auth_session(self, token_hash: str, user_id: str, now: float, expires_at: float) -> None:
        with self._lock, self._sessions.begin() as session:
            # Housekeeping: drop this user's expired sessions.
            session.execute(delete(AuthSessionRow).where(
                AuthSessionRow.user_id == user_id, AuthSessionRow.expires_at <= now))
            session.add(AuthSessionRow(token_hash=token_hash, user_id=user_id, created_at=now, expires_at=expires_at))

    def session_user(self, token_hash: str, now: float) -> User | None:
        """The user a live (unexpired) session belongs to."""
        with self._lock, self._sessions() as session:
            row = session.execute(
                select(UserRow.id, UserRow.name)
                .join(AuthSessionRow, AuthSessionRow.user_id == UserRow.id)
                .where(AuthSessionRow.token_hash == token_hash, AuthSessionRow.expires_at > now)
            ).one_or_none()
            return None if row is None else User(row.id, row.name)

    def delete_auth_session(self, token_hash: str) -> None:
        with self._lock, self._sessions.begin() as session:
            session.execute(delete(AuthSessionRow).where(AuthSessionRow.token_hash == token_hash))

    def user_seats(self, user_id: str) -> list[Seat]:
        """Every seat this user holds, newest game first."""
        with self._lock, self._sessions() as session:
            rows = session.execute(
                select(PlayerRow.game_id, PlayerRow.id, PlayerRow.name, GameRow.status, PlayerRow.is_host,
                       GameRow.created_at)
                .join(GameRow, GameRow.id == PlayerRow.game_id)
                .where(PlayerRow.user_id == user_id)
                .order_by(GameRow.created_at.desc())
            )
            return [Seat(*r) for r in rows]

    def user_seat(self, game_id: str, user_id: str) -> str | None:
        """The player id this user holds in this game, if any."""
        with self._lock, self._sessions() as session:
            return session.scalars(
                select(PlayerRow.id).where(PlayerRow.game_id == game_id, PlayerRow.user_id == user_id)
            ).first()

    def add_seat_token(self, token_hash: str, game_id: str, player_id: str, now: float) -> None:
        with self._lock, self._sessions.begin() as session:
            session.add(SeatTokenRow(token_hash=token_hash, player_id=player_id, game_id=game_id, created_at=now))

    def load_messages(self, game_id: str) -> list[Message]:
        with self._lock, self._sessions() as session:
            q = select(MessageRow).where(MessageRow.game_id == game_id).order_by(MessageRow.id)
            return [_message(r) for r in session.scalars(q)]


# -- schema upgrades -------------------------------------------------------------------------

def _migrate(engine: Engine) -> None:
    """Bring a database made by an older version up to date: ``create_all`` creates
    missing tables but never adds columns to existing ones."""
    with engine.begin() as conn:
        columns = {row[1] for row in conn.execute(text("PRAGMA table_info(players)"))}
        if "user_id" not in columns:
            conn.execute(text("ALTER TABLE players ADD COLUMN user_id VARCHAR REFERENCES users(id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_players_user_id ON players (user_id)"))
        # One seat per account per game. NULLs (anonymous players) never collide.
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS players_game_user ON players (game_id, user_id)"))


# -- row <-> domain conversion ------------------------------------------------------------

def _copy_player(p: Player, row: PlayerRow, seq: int) -> None:
    row.seq = seq
    row.name, row.color = p.name, p.color
    row.x, row.y, row.hp, row.ap, row.action_range = p.x, p.y, p.hp, p.ap, p.action_range
    row.alive, row.is_host = p.alive, p.is_host
    row.died_at, row.joined_jury_at = p.died_at, p.joined_jury_at


def _action_row(game_id: str, e: LogEntry) -> ActionRow:
    return ActionRow(
        game_id=game_id, id=e.id, ts=e.ts, actor_id=e.actor_id, event_type=e.event_type.value, target_id=e.target_id,
        amount=e.amount, from_xy=list(e.from_xy) if e.from_xy else None,
        to_xy=list(e.to_xy) if e.to_xy else None, details=e.details, visibility=e.visibility,
    )


def _message(r: MessageRow) -> Message:
    return Message(r.id, r.game_id, r.ts, r.from_id, r.to_id, r.text)


def _hydrate(session: Session, g: GameRow) -> GameState:
    state = GameState(
        id=g.id, config=GameConfig.from_stored(g.config), created_at=g.created_at, status=g.status,  # type: ignore[arg-type]
        started_at=g.started_at, ended_at=g.ended_at, winner_ids=list(g.winner_ids), end_reason=g.end_reason,
        board_size=g.board_size, last_ap_tick_at=g.last_ap_tick_at, last_jury_cycle_at=g.last_jury_cycle_at,
        jury_cycle_index=g.jury_cycle_index, next_log_id=g.next_log_id,
    )
    state.players = [
        Player(
            id=r.id, name=r.name, color=r.color, x=r.x, y=r.y, hp=r.hp, ap=r.ap, action_range=r.action_range,
            alive=r.alive, is_host=r.is_host, died_at=r.died_at, joined_jury_at=r.joined_jury_at,
        )
        for r in g.players
    ]
    actions = session.scalars(select(ActionRow).where(ActionRow.game_id == g.id).order_by(ActionRow.id))
    state.log = [
        LogEntry(
            id=r.id, ts=r.ts, event_type=EventType(r.event_type), actor_id=r.actor_id, target_id=r.target_id, amount=r.amount,
            from_xy=(r.from_xy[0], r.from_xy[1]) if r.from_xy else None,
            to_xy=(r.to_xy[0], r.to_xy[1]) if r.to_xy else None,
            details=r.details, visibility=r.visibility,  # type: ignore[arg-type]
        )
        for r in actions
    ]
    votes = session.scalars(select(JuryVoteRow).where(JuryVoteRow.game_id == g.id))
    state.jury_votes = [JuryVote(v.cycle_index, v.juror_id, v.candidate_id, v.ts) for v in votes]
    return state

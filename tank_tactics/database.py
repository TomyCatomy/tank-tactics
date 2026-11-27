"""Database engines for the store: SQLite (a file path or ``:memory:``) or Postgres
(a ``postgresql://`` URL, such as Neon's).

On Neon, the app's traffic goes through the pooled URL (``DATABASE_URL``, host with
``-pooler``, PgBouncer in transaction mode) and schema setup goes through the direct
one (``DATABASE_URL_UNPOOLED``) when it is given.
"""
from __future__ import annotations

import os
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool


def is_url(target: str) -> bool:
    return "://" in target


def make_engine(target: str, **connect_args: Any) -> Engine:
    """An engine for a SQLite path or a database URL."""
    if is_url(target):
        return _postgres_engine(target, **connect_args)
    if target == ":memory:":
        # One shared connection, or each session would see its own empty database.
        engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    else:
        os.makedirs(os.path.dirname(os.path.abspath(target)), exist_ok=True)
        engine = create_engine(f"sqlite:///{os.path.abspath(target)}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


def _postgres_engine(url: str, **connect_args: Any) -> Engine:
    # SQLAlchemy's default Postgres driver is psycopg2; use psycopg 3.
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg://" + url.removeprefix(prefix)
    # psycopg prepares statements it runs often; behind PgBouncer in transaction mode
    # a prepared statement may land on another server connection, so never prepare.
    connect_args.setdefault("prepare_threshold", None)
    return create_engine(
        url,
        connect_args=connect_args,
        pool_pre_ping=True,  # Neon suspends idle computes, which closes pooled connections
        pool_recycle=300,
        pool_size=5,
        max_overflow=5,
    )


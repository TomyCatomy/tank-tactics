"""Entry point: ``python -m tank_tactics.server``.

Settings come from environment variables (see ``ServerConfig``), and from ``.env`` and
``.env.local`` in the working directory when they exist (``neon env pull`` writes the
Neon ``DATABASE_URL`` there); the app itself is built in ``tank_tactics.api``.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

import uvicorn

from . import telemetry
from .api import create_app
from .config import ServerConfig
from .database import make_engine
from .service import GameService
from .store import Store

log = logging.getLogger("tank_tactics")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = ServerConfig(_env_file=(".env", ".env.local"))  # type: ignore[call-arg]  # PORT, DB_PATH, DATABASE_URL, ...
    if cfg.database_url:
        engine = make_engine(cfg.database_url)
        schema_engine = make_engine(cfg.database_url_unpooled) if cfg.database_url_unpooled else None
        store = Store(engine, schema_engine)
        if schema_engine:
            schema_engine.dispose()
        where = f"Postgres at {urlsplit(cfg.database_url).hostname}"  # never log the password
    else:
        store, where = Store(make_engine(cfg.db_path)), cfg.db_path
    service = GameService(store, cfg)
    log.info("loaded %d live game(s) from %s", len(service.games), where)
    app = create_app(service)
    tel = telemetry.setup(app)  # off unless OTEL_EXPORTER_OTLP_ENDPOINT is set
    log.info("telemetry export: %s", "on" if tel else "off")
    try:
        uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")
    finally:
        if tel:
            tel.shutdown()


if __name__ == "__main__":
    main()

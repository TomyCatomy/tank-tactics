"""Entry point: ``python -m tank_tactics.server``.

Settings come from environment variables (see ``ServerConfig``); the app itself is
built in ``tank_tactics.api``.
"""
from __future__ import annotations

import logging

import uvicorn

from . import telemetry
from .api import create_app
from .config import ServerConfig
from .service import GameService
from .store import Store

log = logging.getLogger("tank_tactics")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = ServerConfig()  # reads PORT, DB_PATH, ... from the environment
    service = GameService(Store(cfg.db_path), cfg)
    log.info("loaded %d live game(s) from %s", len(service.games), cfg.db_path)
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

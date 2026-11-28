"""The game's own metrics: live games, open WebSockets and game log events."""
from __future__ import annotations

from typing import Iterable

from fastapi import FastAPI
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider

from .. import game


def instrument(app: FastAPI, meter_provider: MeterProvider) -> None:
    """Gauges for live games and open WebSockets, and a counter of game log events by type."""
    service, hub = app.state.service, app.state.hub
    meter = meter_provider.get_meter("tank_tactics")

    def games(_: CallbackOptions) -> Iterable[Observation]:
        counts = {"lobby": 0, "active": 0}
        for state in list(service.games.values()):  # list() copies under the GIL; safe off-thread
            if state.status in counts:
                counts[state.status] += 1
        return [Observation(n, {"status": status}) for status, n in counts.items()]

    def sockets(_: CallbackOptions) -> Iterable[Observation]:
        return [Observation(sum(len(s) for s in list(hub.sockets.values())))]

    meter.create_observable_gauge("tank_tactics.games", [games], unit="{game}", description="Live games by status.")
    meter.create_observable_gauge(
        "tank_tactics.websockets", [sockets], unit="{connection}", description="Open live-update connections."
    )
    events = meter.create_counter(
        "tank_tactics.game_events", unit="{event}", description="Game log entries written, by event type."
    )

    def count(_game_id: str, entries: list[game.LogEntry]) -> None:
        for entry in entries:
            events.add(1, {"event_type": entry.event_type.value})

    hub.listeners.append(count)

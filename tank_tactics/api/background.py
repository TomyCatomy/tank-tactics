"""The time scheduler: applies due AP ticks, jury closes and time limits."""
from __future__ import annotations

import asyncio
import logging

from .hub import Hub

log = logging.getLogger("tank_tactics")


async def run_scheduler(hub: Hub) -> None:
    """Runs once immediately on boot, which catches up any AP occurrences missed while
    the server was down, then sleeps until the next due event or until a change wakes it."""
    svc = hub.service
    max_sleep = svc.cfg.scheduler_max_sleep
    while True:
        try:
            svc.tick()
        except Exception:
            log.exception("scheduler tick failed")
        wait = svc.seconds_until_next_due()
        timeout = max_sleep if wait is None else min(max_sleep, max(0.05, wait + 0.01))
        hub.wake.clear()
        try:
            await asyncio.wait_for(hub.wake.wait(), timeout)
        except TimeoutError:
            pass

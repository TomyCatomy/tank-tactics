"""Discord webhook notifications.

Only public log entries are posted, so a webhook can never leak fog-of-war data.
"""
from __future__ import annotations

import logging

import httpx

from .. import game

log = logging.getLogger("tank_tactics")


def discord_line(state: game.GameState, e: game.LogEntry) -> str | None:
    if e.visibility != "public":
        return None

    def name(pid: str | None) -> str:
        p = game.find_player(state, pid) if pid else None
        return p.name if p else "?"

    if e.event_type == "kill":
        return f"💥 **{name(e.actor_id)}** destroyed **{name(e.target_id)}**."
    if e.event_type == "removed":
        return f"🚫 **{name(e.target_id)}** was removed from the game by the host."
    if e.event_type == "game_start":
        return f"🏁 Tank Tactics game `{state.id}` has started with {len(state.players)} tanks."
    if e.event_type == "game_over":
        winners = ", ".join(f"**{name(w)}**" for w in e.details.get("winners", []))
        return f"🏆 Game over! Winner: {winners}."
    return None


async def post_discord(url: str, lines: list[str]) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"content": "\n".join(lines)[:1900], "allowed_mentions": {"parse": []}})
    except Exception:
        log.warning("discord webhook failed", exc_info=True)

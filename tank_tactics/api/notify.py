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
    if e.event_type == "ap_tick":
        return _ap_line(state, e.amount or 1)
    if e.event_type == "game_start":
        return f"🏁 Tank Tactics game `{state.id}` has started with {len(state.players)} tanks."
    if e.event_type == "game_over":
        winners = ", ".join(f"**{name(w)}**" for w in e.details.get("winners", []))
        return f"🏆 Game over! Winner: {winners}."
    return None


def _ap_line(state: game.GameState, amount: int) -> str:
    capped = " (up to the cap)" if state.config.ap.cap is not None else ""
    return f"⚡ AP handed out: every tank still in the game got **{amount} AP**{capped}."


def discord_lines(state: game.GameState, entries: list[game.LogEntry]) -> list[str]:
    """One line per postable entry, with a run of AP ticks (restart catch-up, fast
    ticks) merged into a single line."""
    lines: list[str] = []
    ap = 0
    for e in entries:
        if e.event_type == "ap_tick" and e.visibility == "public":
            ap += e.amount or 1
            continue
        if ap:
            lines.append(_ap_line(state, ap))
            ap = 0
        line = discord_line(state, e)
        if line:
            lines.append(line)
    if ap:
        lines.append(_ap_line(state, ap))
    return lines


async def post_discord(url: str, lines: list[str]) -> None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"content": "\n".join(lines)[:1900], "allowed_mentions": {"parse": []}})
    except Exception:
        log.warning("discord webhook failed", exc_info=True)

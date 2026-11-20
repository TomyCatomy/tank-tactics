# Implementation notes: ambiguities resolved

Where the spec left room for interpretation, this is what I chose and why.

## Stack

1. **Python backend instead of Node.js.** The project owner asked for Python. I used
   FastAPI on uvicorn (REST and WebSocket), pydantic for configuration and
   SQLAlchemy over SQLite for persistence. The frontend is plain HTML/JS with no build step.

## Rules

2. **Starting spacing.** "Not within distance 2 of another tank" means Chebyshev
   distance ≥ 3. The fallback "relax to distance 1" means ≥ 2, and the last fallback
   allows any free tile. Placement is randomised greedy with 50 attempts per level.
3. **`ap.cap` applies to every AP gain**, not just ticks: loot, gifts and jury awards
   too. Any excess is lost. A gift that runs into the cap still costs the giver the full
   amount; the entry records how much arrived, and only the two participants can see it.
4. **Removing a player mid-game** destroys the tank and zeroes its AP. Nobody fired the
   shot, so nobody gets loot. The removed player becomes a spectator, or a juror if the
   jury is on, and keeps their token so they can still watch.
5. **Jury votes for a player who dies before the cycle closes** don't count.
6. **Jury cycles** start when the game starts and run on their own clock. If an AP
   tick and a jury close fall at the same instant, the tick is applied first.
7. **Time-limit ending:** AP ticks due exactly at the deadline are not applied (the
   game is already over).
8. **Simultaneous death** (safety rule) cannot happen with single shots. The only
   multi-death path is host removal, which removes one tank at a time.
9. **Dead players and chat:** "keep access to the public channel only" is applied
   literally. A destroyed player can't send or receive DMs, and their old DMs are
   hidden from them.
10. **Lobby config changes** reset every joined player's starting stats to the new
    values. Players can only join while the game is in the lobby.
10a. **Leaving and closing a lobby** (not in the spec). Players can leave a lobby; their
    token is revoked and any open page is told it's no longer in the game. The host
    can't leave, because a lobby without a host could never start; they close it instead. Closing is
    final: the game gets status `closed`, joins and starts are refused, and everyone's
    token keeps working so they can see that it was closed. Neither is possible once
    the game has started (mid-game, the host can still remove a player).

## Fog of war

11. **Loot** is logged as a separate `loot` entry with visibility `actor_only`, next to
    the public `kill` entry. The spec's action `type` list became the `EventType` enum
    (field `event_type`) and gained `loot`, plus `join`, `leave`, `config`, `game_start`,
    `removed`, `jury_vote` and `game_over`.
12. **The victim doesn't see their own loot amount either.** They knew their balance
    just before dying, so this is only a formality, but it matches "visible only to the
    killer".
13. **Fog stays on after the game ends.** Hidden information is never revealed, even
    on the game-over screen.
14. **Replay** is rebuilt on the client from the viewer's own redacted log. Public
    shot entries carry `target_hp_after`, so HP changes from gifts the viewer couldn't
    see are corrected at the target's next shot.
15. **Discord webhook** posts only public events (start, kills, removals, game over), so
    it can't leak hidden information. It accepts only `https://discord.com/api/webhooks/...`
    URLs (so the server can't be pointed at arbitrary hosts), and only the host can see
    the configured URL.

## Data model

16. `Game.winner_player_id` became `winner_ids` (a list), because the rules allow
    shared wins. `end_reason` was added.
17. Player tokens are stored as SHA-256 hashes.
18. **Very long downtime:** catch-up applies every whole interval. When more than 50 AP
    ticks are pending in a row, they are logged as one `ap_tick` entry with
    `details.ticks = n` instead of n separate entries.

## Not built (optional or out of scope for v1)

- Email or push notifications. The client shows in-app toasts for all five events in
  4.5 and uses browser notifications when permitted (some mobile browsers need a service
  worker for these). Discord covers public events.
- Accounts. Identity is a per-player token, delivered through the join flow and a
  personal rejoin link.

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
6. **Jury cycles** start when the game starts and close on every occurrence of the AP
   schedule (there is no separate jury interval any more). At each occurrence the AP
   tick is applied first, then the cycle closes, so a jury award can run into `ap.cap`
   after that tick.
7. **Time-limit ending:** AP ticks (and jury closes) due exactly at the deadline are
   not applied (the game is already over). The deadline is `max_duration_seconds` after
   the start, rounded up to the next round minute like every scheduled event (see 35),
   so a tick on that minute is the one that doesn't happen. With debug fast ticks the
   deadline is exact.
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
18. **Very long downtime:** catch-up applies every schedule occurrence that has passed.
    When more than 50 AP ticks are pending in a row, they are logged as one `ap_tick`
    entry with `details.ticks = n` (at the time of the last one) instead of n separate
    entries. Occurrences are counted without listing them: plain intervals by
    arithmetic, other rules by lazy iteration from cached checkpoints. With the jury on,
    every tick is followed by a jury close, so ticks are never collapsed (as before).

## AP schedule (recurrence rules)

26. **The schedule is an RFC 5545 RRULE body** (`ap.rrule`) in an IANA zone
    (`ap.timezone`), with DTSTART = the moment the game starts, in that zone, floored to
    the minute (see 35). The default is `FREQ=DAILY` in UTC: one AP a day, at the start's
    hh:mm:00, which is the old 24-hour interval to within a minute. The web client starts
    new games in the browser's zone; the server default stays UTC (it has no better
    source). In a zone with DST, plain `FREQ=DAILY` keeps the start's wall-clock time, so
    one day a year is 23 hours and one 25.
27. **What a rule may contain.** No `DTSTART` (the game start supplies it), no `COUNT` or
    `UNTIL`: the game ends the schedule, and a schedule that stopped would leave a game
    with no more AP. So the builder has no end-condition section. Also refused: unknown
    parts (including `X-` and dateutil's `BYEASTER`), `BYWEEKNO` and `BYYEARDAY` (yearly
    concepts, see 28), repeated parts, numbered `BYDAY` outside monthly rules, `BYSETPOS`
    alone, and any `BYSECOND` but 0. dateutil (`rrulestr`) parses the rule and the game
    adds these checks on top. The rule is stored in dateutil's canonical spelling, so an
    optional `RRULE:` prefix is dropped and parts may be reordered (`BYDAY=TH,MO` becomes
    `BYDAY=MO,TH`).
28. **Cadence: at most once a minute, at least once every calendar month.**
    - *Minimum:* ticks are on round minutes (35), so at most one a minute.
      `FREQ=SECONDLY` is refused for new games, even on `ALLOW_DEBUG` servers. The only
      faster ticks are `debug.fast_ticks` (31), which are unchanged.
    - *Maximum:* the schedule has to fire in every calendar month, and never more than 35
      days apart (plus an hour for a DST change in between). Monthly by day of the month
      (1-28), the last day (`BYMONTHDAY=-1`), the nth weekday (`BYDAY=TH;BYSETPOS=4` or
      `4TH`) and the last weekday (`-1`) are all fine; "the last Thursday" can be 35 days
      apart. Refused: `FREQ=YEARLY`, `FREQ=MONTHLY` with `INTERVAL` above 1, and anything
      that skips a month, such as `BYMONTHDAY=29`/`30`/`31`, a fifth weekday, `BYMONTH`
      leaving months out, or `FREQ=WEEKLY;INTERVAL=5` (35 days, but it can jump a whole
      month). The error suggests `BYMONTHDAY=-1` or `-1<weekday>` where those fit. A
      monthly rule must say which day (`BYMONTHDAY` or `BYDAY`): plain `FREQ=MONTHLY`
      would take the start date's day and skip months for a game started on the 31st.
      Plain intervals can be at most 28 days (672 hours), the longest step that never
      skips February.
    - *How it's checked:* the rule is expanded from three reference starts (midnight on
      1 January, mid-July mid-day, and the last minute of a 31st) for 26 months. Every
      whole calendar month in that window needs a tick, and no gap (including the wait
      for the first tick) can exceed 35 days. A rule with so many ticks a day that the
      sample (3,000) runs out first is accepted only if it has no month-level parts
      (`BYMONTH`, `BYMONTHDAY`, `BYSETPOS`, numbered `BYDAY`), because then every month
      looks alike. Rules that never fire are refused. dateutil has no search bound for
      those, so common cases are caught statically and the rest under a 0.5 s budget.
    - *The lobby editor:* the presets and builder only offer rules that pass: monthly
      every 1 month on day 1-28, the last day, the 1st-4th or last weekday; at most every
      4 weeks, 28 days or 672 hours.
29. **DST.** Occurrences are wall-clock times in the zone. A time that doesn't exist (in
    a spring-forward gap) happens later by the gap's length (02:30 becomes 03:30), as
    RFC 5545 says. A repeated time (fall-back) happens once, at its first occurrence.
    Two occurrences landing on the same instant count once.
30. **Two departures from RFC 5545, for intuitive rules.** (a) Plain sub-daily intervals
    (`FREQ=HOURLY;INTERVAL=6`, `FREQ=MINUTELY;INTERVAL=15`, with no BY parts) count real
    elapsed time from the start minute, so DST never makes one longer or shorter. (b) Time
    parts finer than the finest one given default to zero instead of to the start's:
    `BYHOUR=9` means 09:00:00, not 09:<start minute>:<start second>. This doesn't apply
    to the part `FREQ` repeats (`FREQ=MINUTELY;BYHOUR=9` is every minute from 09:00 to
    09:59). Round minutes (35) are a third rule on top.
31. **`debug.fast_ticks`** replaces the rule with `FREQ=SECONDLY;INTERVAL=<tick_seconds>`
    in UTC for both AP and jury, counted from the exact start (no round minutes), and the
    time limit stays exact. The configured rule is kept and shown alongside. This is the
    only way to get ticks more often than once a minute.
32. **No backwards compatibility** (the owner's call). `ap.interval_seconds` and
    `jury.interval_seconds` are gone with no conversion: a request that sends them gets
    the usual unknown-field 400, and a game stored with them loads with the default
    schedule (`from_stored` drops removed fields), so a daily game keeps ticking daily,
    on the start's minute.
33. **Preview.** `POST /api/schedule/preview` validates exactly as the lobby does and
    returns the normalized rule and its summary. The client uses it for the live summary
    and raw-rule errors. It doesn't list upcoming ticks: the lobby used to show the next 5,
    and the owner removed that. The client only parses and writes rules (with rrule.js,
    vendored in `public/vendor/`) to map them to the presets and builder; validation and
    summaries come from the server. It is rate limited per client IP.
34. **Lobby editor round trip.** The rule string is the source of truth. A rule the presets
    or builder can express exactly is shown there (e.g. `BYDAY=-1FR` shows as "Monthly,
    last Friday"), and the stored text isn't changed until the host edits it through
    them (the builder then writes `BYDAY=FR;BYSETPOS=-1`). Anything else shows as "Custom
    rule" and is never rewritten. Preset labels come from today's date in the chosen zone,
    or from the rule's own weekday or day when it has them.

35. **Round minutes.** Every scheduled event happens on a whole minute (second 0): AP
    ticks, jury closes (they follow the same schedule) and the time limit. DTSTART is the
    game start floored to the minute, `BYSECOND` is implicitly 0, and only occurrences
    strictly after the real start count. So plain `FREQ=DAILY` from a 14:37:23 start
    ticks at 14:37:00 every day, the first one 23 seconds short of a day. The deadline is
    rounded up, never down, so a game is never shorter than its limit. The exceptions are
    debug fast ticks (31), whose timing is exact.

## Not built (optional or out of scope for v1)

- Email or push notifications. The client shows in-app toasts for all five events in
  4.5 and uses browser notifications when permitted (some mobile browsers need a service
  worker for these). Discord covers public events.
- Required accounts. Identity is still a per-player token, delivered through the join
  flow and a personal rejoin link. Google sign-in is an optional layer on top (below).

## Accounts (optional Google sign-in)

19. **Accounts sit above seats, not in place of them.** Each seat keeps its own bearer
    token, so the game API, the WebSocket protocol, the bots and anonymous play are
    unchanged. A session token (from `/api/auth/google`) only lists the account's seats,
    links new seats, and issues extra seat tokens (`/claim`) for another device.
20. **Claiming adds a token; it doesn't rotate.** The original token and rejoin links
    keep working, and extra tokens are stored in `seat_tokens`, deleted with the player.
21. **One seat per account per game**, enforced by a unique index on `(game_id, user_id)`.
    Joins sent without a session (e.g. debug local seats) are unaffected.
22. **No email is stored**, only Google's `sub` and the profile name, which pre-fills
    the name box. Player names in games are still chosen per game, and the account never
    appears in game views.
23. **Expired session on create/join is a 401**, not a silent anonymous join, so a player
    never ends up with an unlinked seat they think is linked.
24. **`Referrer-Policy` is `strict-origin-when-cross-origin`** (was `no-referrer`), because
    Google's sign-in button needs the page origin. Only the origin is sent cross-origin,
    and rejoin tokens are in the URL fragment, which is never sent.
25. **Schema upgrade:** `create_all` adds the new tables, and `store._migrate` adds
    `players.user_id` to databases from before accounts.

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
   (Spacing stays on step distance; range is straight-line, see 36, and moves are
   orthogonal only, see 37.)
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
10b. **Defaults that differ from the spec** (the owner's call). New games start every tank
    with **0 AP**, looting is **off** and the jury is **on**. The host can change all three
    in the lobby.
10c. **The lobby form shows the time limit in whole days.** The config and API keep
    `max_duration_seconds`, so stored games and API clients are unchanged. The form also
    leaves out `board.size_multiplier`; it keeps its default unless set through the API.

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
15. **Discord webhook** posts only public events (start, AP ticks, kills, removals,
    game over), so it can't leak hidden information. An AP tick says how much AP every
    living tank got, never anyone's balance, and a run of ticks applied together (fast
    ticks, restart catch-up) is one line. It accepts only
    `https://discord.com/api/webhooks/...` URLs (so the server can't be pointed at
    arbitrary hosts), and only the host can see the configured URL.
    Hosts normally get one with **Connect to Discord** in the lobby (Discord's OAuth2
    `webhook.incoming` flow: Discord shows a server and channel picker and creates the
    webhook). Like every setting it is lobby-only. The OAuth `state` is single-use and
    expires after 10 minutes, and Discord only redirects to the app's registered URLs.
    The state is also bound to the browser that started it, through an HttpOnly cookie
    sent only to the callback, so a host can't send the Discord link to someone else and
    get their channel. Only the latest attempt per browser works.
    Pasting a URL still works as a fallback. Pending attempts live in memory, so a
    restart mid-way just means clicking the button again.

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

## Range

36. **Range is straight-line (Euclidean) distance** between tile centres, the owner's
    call: a target is in range when `dx² + dy² <= range²`, checked in integers so there
    is no rounding at the edge. Range 3 reaches (3, 0) and (2, 2) but not (3, 1) or
    (2, 3). Only range changed: moving is still one step to any of the 8 neighbours, and
    starting spacing (2) still uses step (Chebyshev) distance. Out-of-range errors show
    the distance to one decimal. With the longer reach of a disc this size, the defaults
    moved to range 3 and an auto board of at least 12×12.

38. **Range upgrades get dearer.** The owner's call: each upgrade costs `range_upgrade_step`
    (default 1) more AP than the one before, so 3, 4, 5... with the defaults. The count of
    upgrades bought is `action_range - start.action_range` (nothing else changes range), so
    no new state is stored. Players' views carry `upgrade_cost`, their next upgrade's price;
    it is public, since it follows from the range, which is public.

## Tank colours

39. **Players pick their tank's colour, in the lobby only**, so tanks don't change colour
    mid-game and confuse everyone. New players still get the next free palette colour; the
    palette is in `GET /api/defaults` (`colors`) for the picker, but any `#rrggbb` is
    accepted. A colour is refused if it is too dark for the board (relative luminance
    under 0.04) or within a "redmean" distance of 60 of another player's, so nobody can
    disguise themselves as someone else. The check only applies to the colour being
    chosen: the palette itself has a few close pairs, which stay as they are. Changing
    colour logs nothing.

## Movement

37. **No diagonal moves**, the owner's call: a move goes to one of the 4 orthogonal
    neighbours (`|dx| + |dy| = 1`); anything else is `not_adjacent`. This replaces the
    8-neighbour moves in 36. Start spacing still uses step (Chebyshev) distance.

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
25. **Schema upgrade:** `create_all` adds the new tables. A hand-written step that added
    `players.user_id` to databases from before accounts ran on every boot until the
    move to Postgres; it was then removed as no longer needed (the deployed database was
    upgraded by it long before), so there are no schema upgrades. The one-seat-per-account
    unique index (`players_game_user`) is declared on the model instead.
26. **Resetting a rejoin link rotates every token for the seat.** `reset-link` replaces the
    original token, deletes claimed ones and closes the seat's sockets, so a leaked link,
    or a device the player no longer has, loses access. It also unlinks the seat from its
    account, because a link made with a leaked token (a signed-in stranger who opened it)
    would otherwise let that account claim a fresh token. The client relinks the new token
    when it is signed in, so a player resetting their own linked seat keeps the link.
    Anyone holding a valid token can reset, as anyone holding one can already play the
    tank: whoever resets first keeps the seat.
38. **Joining from an invite link with Google skips the name box.** Signed out, the join
    page offers Google's button as an alternative to typing a name. Signing in there joins
    at once under the Google profile name (cut to the 24-character limit), or reopens the
    account's existing seat if it already has one in that game. If the join fails (say the
    name is taken), the page shows the signed-in form prefilled with that name to change.
39. **A strict Content-Security-Policy with Trusted Types** (`api/security.py`) backs up
    the client's no-`innerHTML` rule, because seat and session tokens sit in
    localStorage where any injected script could read them. Only our scripts and
    Google's sign-in library run; scripts get no `unsafe-inline` or `unsafe-eval` (inline
    styles are allowed, as Google's button sets some and CSS can't run code), and the
    one Trusted Types policy (`public/js/trusted.js`) only allows Google's script URL.
    The API docs at `/docs` get a looser policy (Swagger UI loads from jsdelivr with an
    inline script); they carry no tokens. HSTS is sent everywhere and ignored over the
    plain-http localhost.

"""The RRULE-based AP schedule: parsing, validation, DST, and how the clock applies it."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tank_tactics import game, recurrence
from tank_tactics.config import ConfigError, GameConfig

from ..support import DAY, T0, make_active, p

JLM = ZoneInfo("Asia/Jerusalem")
NYC = ZoneInfo("America/New_York")


def at(zone: ZoneInfo, *args: int) -> float:
    return datetime(*args, tzinfo=zone).timestamp()


def local(t: float, zone: ZoneInfo) -> str:
    return datetime.fromtimestamp(t, zone).isoformat()


# -- parsing and validation ----------------------------------------------------------------

@pytest.mark.parametrize("rule, message", [
    ("", "is empty"),
    ("BYHOUR=9", "FREQ is required"),
    ("FREQ=SOMETIMES", "invalid 'FREQ': SOMETIMES"),
    ("FREQ=DAILY;COUNT=3", "COUNT is not allowed"),
    ("FREQ=DAILY;UNTIL=20300101T000000Z", "UNTIL is not allowed"),
    ("DTSTART:20260101T090000Z\nRRULE:FREQ=DAILY", "DTSTART is not allowed"),
    ("FREQ=DAILY;DTSTART=20260101T090000Z", "DTSTART is not allowed"),
    ("FREQ=DAILY;BYHOUR=24", "hour must be in 0..23"),
    ("FREQ=DAILY;BYHOUR=nine", "invalid 'BYHOUR': NINE"),
    ("FREQ=DAILY;BYMONTHDAY=0", "BYMONTHDAY values must be between -31 and 31, not 0"),
    ("FREQ=DAILY;BYMONTH=13", "BYMONTH values must be between 1 and 12"),
    ("FREQ=DAILY;INTERVAL=0", "INTERVAL must be between"),
    ("FREQ=DAILY;FREQ=WEEKLY", "FREQ is given twice"),
    ("FREQ=DAILY;X-NAME=1", "unsupported part 'X-NAME'"),
    ("FREQ=DAILY;BYHOUR", "is not KEY=VALUE"),
    ("FREQ=WEEKLY;BYDAY=2MO", "numbered BYDAY values"),
    ("FREQ=WEEKLY;BYDAY=XX", "invalid 'BYDAY': XX"),
    ("FREQ=DAILY;BYEASTER=0", "unsupported part 'BYEASTER'"),
    ("FREQ=SECONDLY;BYSECOND=0", "FREQ=SECONDLY can only be a plain interval"),
    ("FREQ=MONTHLY;BYSETPOS=1", "BYSETPOS needs another BY... part"),
    ("FREQ=MONTHLY;BYMONTH=2;BYMONTHDAY=30", "never fires"),
    ("FREQ=MONTHLY;BYDAY=MO;BYSETPOS=6", "never fires"),
    # At most a minute apart, on round minutes:
    ("FREQ=SECONDLY;INTERVAL=59", "FREQ=SECONDLY is not allowed"),
    ("FREQ=SECONDLY;INTERVAL=3600", "FREQ=SECONDLY is not allowed"),  # even a whole number of minutes
    ("FREQ=HOURLY;BYMINUTE=0;BYSECOND=30", "BYSECOND can only be 0"),
    ("FREQ=MINUTELY;BYSECOND=0,30", "BYSECOND can only be 0"),
    # At least once in every calendar month:
    ("FREQ=YEARLY", "FREQ=YEARLY fires less than once a month"),
    ("FREQ=YEARLY;BYMONTH=1,2,3,4,5,6,7,8,9,10,11,12;BYMONTHDAY=1", "FREQ=YEARLY fires less than once a month"),
    ("FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=1", "INTERVAL above 1 skips months"),
    ("FREQ=MONTHLY", "needs BYMONTHDAY or BYDAY"),  # the day would come from the start date
    ("FREQ=MONTHLY;BYMONTHDAY=31", "Use BYMONTHDAY=-1 for the last day"),
    ("FREQ=MONTHLY;BYMONTHDAY=30;BYHOUR=9", "between ticks: at most 35 days are allowed, so it fires every month. "
                                            "Use BYMONTHDAY=-1"),
    ("FREQ=MONTHLY;BYMONTHDAY=29", "Use BYMONTHDAY=-1"),
    ("FREQ=MONTHLY;BYDAY=5TH", "Use -1 for the last of that weekday"),
    ("FREQ=MONTHLY;BYDAY=TH;BYSETPOS=5", "Use -1 for the last of that weekday"),
    ("FREQ=MONTHLY;BYMONTH=1,3,5,7,9,11;BYMONTHDAY=1", "at most 35 days"),
    ("FREQ=WEEKLY;INTERVAL=6;BYDAY=MO", "at most 35 days"),
    ("FREQ=WEEKLY;INTERVAL=5;BYDAY=MO", "skips months (no tick in August 2024"),  # 35 days apart: jumps a month
    ("FREQ=DAILY;INTERVAL=30", "skips months (no tick in February"),
    ("FREQ=HOURLY;INTERVAL=673", "less than once a month"),
    ("FREQ=WEEKLY;BYWEEKNO=10;BYDAY=MO", "BYWEEKNO is not supported"),
    ("FREQ=DAILY;BYYEARDAY=100", "BYYEARDAY is not supported"),
    ("FREQ=MINUTELY;BYMONTH=1", "can't be checked"),  # too many ticks a day to sample a whole year
])
def test_invalid_rules_are_rejected_with_a_reason(rule, message):
    with pytest.raises(ConfigError, match="ap.rrule") as e:
        GameConfig.parse({"ap": {"rrule": rule}})
    assert message in str(e.value)


@pytest.mark.parametrize("rule", [
    "FREQ=DAILY", "FREQ=MINUTELY", "FREQ=MINUTELY;INTERVAL=5;BYHOUR=9", "FREQ=HOURLY;INTERVAL=6;BYMINUTE=30",
    "FREQ=HOURLY;INTERVAL=672", "FREQ=DAILY;INTERVAL=28;BYHOUR=9", "FREQ=WEEKLY;INTERVAL=4;BYDAY=MO",
    "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,FR;BYHOUR=9", "FREQ=MONTHLY;BYMONTHDAY=28", "FREQ=MONTHLY;BYMONTHDAY=1",
    "FREQ=MONTHLY;BYMONTHDAY=-1", "FREQ=MONTHLY;BYDAY=-1FR;BYHOUR=20", "FREQ=MONTHLY;BYDAY=TH;BYSETPOS=-1",
    "FREQ=MONTHLY;BYDAY=TH;BYSETPOS=4;BYHOUR=9", "FREQ=MONTHLY;BYDAY=1MO", "FREQ=HOURLY;BYMINUTE=0;BYSECOND=0",
    "FREQ=MONTHLY;BYMONTH=1,2,3,4,5,6,7,8,9,10,11,12;BYMONTHDAY=15",
])
def test_valid_rules_are_accepted(rule):
    GameConfig.parse({"ap": {"rrule": rule, "timezone": "Europe/London"}})


@pytest.mark.parametrize("tz", ["Mars/Olympus_Mons", "", "../etc/passwd", "utc+2"])
def test_invalid_time_zones_are_rejected(tz):
    with pytest.raises(ConfigError, match="ap.timezone"):
        GameConfig.parse({"ap": {"timezone": tz}})


def test_rules_are_stored_in_dateutils_canonical_spelling():
    c = GameConfig.parse({"ap": {"rrule": " rrule:freq=weekly;byday=th,mo;byhour=9; ", "timezone": "Asia/Jerusalem"}})
    assert c.ap.rrule == "FREQ=WEEKLY;BYDAY=MO,TH;BYHOUR=9"
    assert recurrence.normalize_rule("FREQ=MONTHLY;BYDAY=TH;BYSETPOS=-1") == "FREQ=MONTHLY;BYSETPOS=-1;BYDAY=TH"
    with pytest.raises(ConfigError):
        GameConfig.parse({"ap": {"rrule": 5}})  # strict: no coercion


def test_default_schedule_is_daily_at_the_start_minute():
    c = GameConfig()
    assert (c.ap.rrule, c.ap.timezone) == ("FREQ=DAILY", "UTC")
    s = make_active({"a": (0, 0), "b": (5, 5)})  # T0 is a round minute
    assert game.next_ap_tick_at(s) == T0 + DAY
    game.advance_time(s, T0 + DAY, random.Random(0))
    assert game.next_ap_tick_at(s) == T0 + 2 * DAY
    # From a start at 14:37:23 the ticks are at 14:37:00, so the first one is 23 s short of a day.
    start = datetime(2026, 11, 26, 14, 37, 23, 500_000, tzinfo=ZoneInfo("UTC")).timestamp()
    s = make_active({"a": (0, 0), "b": (5, 5)}, now=start)
    first = game.next_ap_tick_at(s)
    assert local(first, ZoneInfo("UTC")) == "2026-11-27T14:37:00+00:00" and first - start == DAY - 23.5


def test_summaries():
    tz = "Asia/Jerusalem"
    cases = {
        "FREQ=DAILY": "Every day at the game's start time (to the minute)",
        "FREQ=DAILY;BYHOUR=9;BYMINUTE=0": "Every day at 09:00",
        "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=9;BYMINUTE=0": "Every weekday (Mon–Fri) at 09:00",
        "FREQ=WEEKLY;BYDAY=TH;BYHOUR=9;BYMINUTE=0": "Weekly on Thursday at 09:00",
        "FREQ=WEEKLY;INTERVAL=2;BYDAY=FR,MO;BYHOUR=18;BYMINUTE=30": "Every 2 weeks on Monday, Friday at 18:30",
        "FREQ=MONTHLY;BYMONTHDAY=26;BYHOUR=9;BYMINUTE=0": "Monthly on day 26 at 09:00",
        "FREQ=MONTHLY;BYDAY=TH;BYSETPOS=4;BYHOUR=9;BYMINUTE=0": "Monthly on the fourth Thursday at 09:00",
        "FREQ=MONTHLY;BYDAY=TH;BYSETPOS=-1;BYHOUR=9;BYMINUTE=0": "Monthly on the last Thursday at 09:00",
        "FREQ=HOURLY;INTERVAL=6;BYMINUTE=15": "Every 6 hours at minute 15",
        "FREQ=HOURLY;INTERVAL=6": "Every 6 hours",
        "FREQ=SECONDLY;INTERVAL=86400": "Every 24 hours",
        "FREQ=SECONDLY;INTERVAL=10": "Every 10 seconds",
        "FREQ=MONTHLY;BYMONTHDAY=-1;BYHOUR=20;BYMINUTE=0": "Monthly on the last day at 20:00",
        "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9,17;BYMINUTE=0": "Weekly on Monday at 09:00, 17:00",
        "FREQ=MINUTELY;INTERVAL=5;BYHOUR=9": "Custom rule: FREQ=MINUTELY;INTERVAL=5;BYHOUR=9",
    }
    for rule, text in cases.items():
        assert recurrence.describe(rule, tz) == f"{text} ({tz})", rule


# -- occurrences and DST ---------------------------------------------------------------------

def test_daily_time_stays_local_across_dst_in_jerusalem():
    # Israel moved to summer time on Friday 27 March 2026 and back on Sunday 25 October 2026.
    start = at(JLM, 2026, 3, 25, 12, 0)
    s = make_active({"a": (0, 0), "b": (5, 5)}, now=start,
                    ap={"rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "timezone": "Asia/Jerusalem"})
    ticks = game.schedule_of(s).upcoming(start, 4)
    assert [local(t, JLM) for t in ticks] == [
        "2026-03-26T09:00:00+02:00", "2026-03-27T09:00:00+03:00", "2026-03-28T09:00:00+03:00",
        "2026-03-29T09:00:00+03:00"]
    assert ticks[1] - ticks[0] == 23 * 3600  # the short day
    autumn = game.schedule_of(s).upcoming(at(JLM, 2026, 10, 24, 12, 0), 2)
    assert [local(t, JLM) for t in autumn] == ["2026-10-25T09:00:00+02:00", "2026-10-26T09:00:00+02:00"]
    assert autumn[0] - at(JLM, 2026, 10, 24, 9, 0) == 25 * 3600  # the long day


def test_nonexistent_and_repeated_wall_times():
    # New York 2026: clocks jump 02:00 -> 03:00 on 8 March and 02:00 -> 01:00 on 1 November.
    gap = recurrence.Schedule("FREQ=DAILY;BYHOUR=2;BYMINUTE=30", "America/New_York", at(NYC, 2026, 3, 7, 12, 0))
    assert [local(t, NYC) for t in gap.upcoming(gap.dtstart, 2)] == [
        "2026-03-08T03:30:00-04:00", "2026-03-09T02:30:00-04:00"]  # 02:30 doesn't exist: it happens an hour later
    fold = recurrence.Schedule("FREQ=DAILY;BYHOUR=1;BYMINUTE=30", "America/New_York", at(NYC, 2026, 10, 31, 12, 0))
    assert [local(t, NYC) for t in fold.upcoming(fold.dtstart, 2)] == [
        "2026-11-01T01:30:00-04:00", "2026-11-02T01:30:00-05:00"]  # 01:30 happens twice: only the first counts
    hourly = recurrence.Schedule("FREQ=HOURLY;BYMINUTE=0", "America/New_York", at(NYC, 2026, 3, 8, 0, 30))
    got = hourly.upcoming(hourly.dtstart, 3)
    assert [local(t, NYC) for t in got] == [
        "2026-03-08T01:00:00-05:00", "2026-03-08T03:00:00-04:00", "2026-03-08T04:00:00-04:00"]  # no duplicate


def test_pure_sub_daily_intervals_count_real_time():
    s = recurrence.Schedule("FREQ=HOURLY;INTERVAL=6", "America/New_York", at(NYC, 2026, 3, 7, 22, 0))
    got = s.upcoming(s.dtstart, 3)
    assert [b - a for a, b in zip([s.dtstart, *got], got)] == [6 * 3600] * 3  # DST doesn't stretch them


def test_schedule_matches_a_brute_force_reference():
    """Restarting iteration from cached checkpoints and reordering around DST changes
    must give exactly the occurrences dateutil lists from DTSTART."""
    rules = ["FREQ=DAILY;BYHOUR=2;BYMINUTE=30", "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,FR;BYHOUR=9",
             "FREQ=MONTHLY;BYMONTHDAY=31", "FREQ=MONTHLY;INTERVAL=2;BYDAY=TH;BYSETPOS=-1;BYHOUR=1;BYMINUTE=30",
             "FREQ=HOURLY;INTERVAL=5;BYMINUTE=0,45", "FREQ=MINUTELY;INTERVAL=25;BYHOUR=1,2,3"]
    rng = random.Random(3)
    for tz in ("Asia/Jerusalem", "America/New_York", "Australia/Lord_Howe", "UTC"):
        zone = ZoneInfo(tz)
        for rule in rules:
            start = 1_760_000_000 + rng.randrange(30 * DAY) + 0.5
            end = start + 400 * DAY
            raw = recurrence._compile(rule, recurrence._wall(int(start), zone))
            ref = []
            for w in raw:
                if w > recurrence._wall(end, zone) + timedelta(hours=4):
                    break
                e = recurrence._epoch(w, zone)
                if start < e <= end:
                    ref.append(e)
            ref = sorted(set(ref))
            s = recurrence.Schedule(rule, tz, start)
            early = [x for x in ref if x < start + 150 * DAY]  # leave room for 5 more inside the window
            for _ in range(30):
                q = rng.choice(early) if rng.random() < 0.5 else start + rng.random() * 150 * DAY
                expect = [x for x in ref if x > q][:5]
                assert s.upcoming(q, len(expect)) == expect, (tz, rule, q)
            n, first, last = s.between(start, end, 51)
            assert (n, first, last) == (len(ref), ref[:51], ref[-1]), (tz, rule)


# -- the clock ------------------------------------------------------------------------------

def _jury_game(start: float, **config):
    s = make_active({"a": (0, 0), "b": (9, 9), "c": (0, 9)}, now=start, jury={"enabled": True}, **config)
    game.remove_player(s, "a", "c", start)  # c becomes a juror
    return s


def test_ap_tick_comes_before_the_jury_close_at_the_same_occurrence():
    start = at(JLM, 2026, 11, 26, 12, 0)
    s = _jury_game(start, ap={"rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "timezone": "Asia/Jerusalem", "cap": 2})
    p(s, "b").ap = 2  # at the cap: the tick is lost, then the jury award is lost too
    p(s, "a").ap = 1
    game.cast_jury_vote(s, "c", "a", start + 60)
    first = at(JLM, 2026, 11, 27, 9, 0)
    assert game.next_ap_tick_at(s) == game.next_jury_close_at(s) == first
    entries = game.advance_time(s, first, random.Random(0))
    assert [(e.event_type, e.ts) for e in entries] == [("ap_tick", first), ("jury_award", first)]
    # a: 1 + tick = 2 (the cap), so the jury's +1 is lost; it would have counted had the jury gone first.
    assert p(s, "a").ap == 2
    assert s.last_jury_cycle_at == first and s.jury_cycle_index == 1
    assert game.next_jury_close_at(s) == at(JLM, 2026, 11, 28, 9, 0)


def test_jury_disabled_never_closes():
    s = make_active({"a": (0, 0), "b": (5, 5)}, ap={"rrule": "FREQ=HOURLY;BYMINUTE=0"})
    assert game.next_jury_close_at(s) is None
    entries = game.advance_time(s, T0 + 3 * DAY, random.Random(0))
    assert {e.event_type for e in entries} == {"ap_tick"}


def test_fast_ticks_override_the_rule_for_ap_and_jury():
    start = at(JLM, 2026, 11, 26, 12, 0)
    s = _jury_game(start, ap={"rrule": "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9", "timezone": "Asia/Jerusalem"},
                   debug={"fast_ticks": True, "tick_seconds": 10})
    assert game.next_ap_tick_at(s) == game.next_jury_close_at(s) == start + 10
    game.cast_jury_vote(s, "c", "b", start + 1)
    entries = game.advance_time(s, start + 35, random.Random(0))
    assert [(e.event_type, e.ts - start) for e in entries] == [
        ("ap_tick", 10), ("jury_award", 10), ("ap_tick", 20), ("ap_tick", 30)]  # later cycles had no votes
    assert s.jury_cycle_index == 3
    view = game.view_for(s, "a")["game"]["schedule"]
    assert view["rrule"] == "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9"
    assert view["effective_rrule"] == "FREQ=SECONDLY;INTERVAL=10" and view["effective_timezone"] == "UTC"
    assert view["effective_summary"] == "Every 10 seconds (UTC) (debug fast ticks)"


def test_long_downtime_catch_up_is_collapsed_and_bounded():
    start = at(JLM, 2026, 1, 1, 12, 0)
    s = make_active({"a": (0, 0), "b": (5, 5)}, now=start,
                    ap={"rrule": "FREQ=WEEKLY;BYDAY=MO,TH;BYHOUR=9;BYMINUTE=0", "timezone": "Asia/Jerusalem"})
    later = at(JLM, 2036, 1, 1, 12, 0)  # ten years down
    entries = game.advance_time(s, later, random.Random(0))
    days = (datetime(2036, 1, 1) - datetime(2026, 1, 2)).days  # 2 Jan 2026 .. 31 Dec 2035
    expected = sum((datetime(2026, 1, 2) + timedelta(d)).weekday() in (0, 3) for d in range(days))
    assert expected > 1000
    assert len(entries) == 1 and entries[0].details["ticks"] == expected and entries[0].amount == expected
    assert p(s, "a").ap == 1 + expected
    assert local(s.last_ap_tick_at, JLM) == "2035-12-31T09:00:00+02:00" == local(entries[0].ts, JLM)
    assert game.advance_time(s, later, random.Random(0)) == []


def test_short_catch_up_logs_each_tick():
    s = make_active({"a": (0, 0), "b": (5, 5)}, ap={"rrule": "FREQ=HOURLY;INTERVAL=2"})
    entries = game.advance_time(s, T0 + 50 * 2 * 3600 + 5, random.Random(0))  # T0 is a round minute
    assert len(entries) == 50 and [e.ts for e in entries][:2] == [T0 + 7200, T0 + 14400]


def test_catch_up_with_jury_interleaves_every_occurrence():
    start = at(JLM, 2026, 11, 26, 12, 0)
    s = _jury_game(start, ap={"rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "timezone": "Asia/Jerusalem"})
    entries = game.advance_time(s, start + 60 * DAY, random.Random(0))
    ticks = [e for e in entries if e.event_type == "ap_tick"]
    assert len(ticks) == 60 and all(e.amount == 1 for e in ticks)  # never collapsed: a jury close follows each
    assert s.jury_cycle_index == 60 and p(s, "a").ap == 1 + 60


def test_time_limit_exactly_on_an_occurrence():
    start = at(JLM, 2026, 11, 26, 9, 0)
    s = _jury_game(start, ap={"rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "timezone": "Asia/Jerusalem"},
                   max_duration_seconds=2 * DAY)  # ends exactly at the second occurrence
    entries = game.advance_time(s, start + 10 * DAY, random.Random(0))
    assert [(e.event_type, e.ts - start) for e in entries] == [
        ("ap_tick", DAY), ("game_over", 2 * DAY)]  # no tick or jury close at the deadline (NOTES 7)
    assert s.jury_cycle_index == 1 and s.status == "finished"
    assert p(s, "a").ap == 2


def test_time_limit_inside_a_collapsed_catch_up():
    s = make_active({"a": (0, 0), "b": (5, 5)}, ap={"rrule": "FREQ=HOURLY"}, max_duration_seconds=100 * 3600)
    entries = game.advance_time(s, T0 + 1000 * 3600, random.Random(0))
    assert entries[0].details["ticks"] == 99 and entries[0].ts == T0 + 99 * 3600
    assert entries[1].event_type == "game_over" and entries[1].ts == T0 + 100 * 3600


def test_view_lists_the_schedule_and_next_occurrences():
    start = at(JLM, 2026, 11, 26, 12, 0)
    s = make_active({"a": (0, 0), "b": (5, 5)}, now=start,
                    ap={"rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "timezone": "Asia/Jerusalem"},
                    max_duration_seconds=3 * DAY)
    sched = game.view_for(s, "a")["game"]["schedule"]
    assert sched["rrule"] == sched["effective_rrule"] == "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    assert sched["timezone"] == "Asia/Jerusalem"
    assert sched["summary"] == "Every day at 09:00 (Asia/Jerusalem)"
    # Only occurrences before the time limit (26th 12:00 + 3 days).
    assert [local(t, JLM)[:16] for t in sched["upcoming"]] == ["2026-11-27T09:00", "2026-11-28T09:00", "2026-11-29T09:00"]
    lobby = game.create_game("x", "h", "H", GameConfig(), T0)
    assert game.view_for(lobby, "h")["game"]["schedule"]["upcoming"] == []


def test_preview_flags_dst_shifts():
    occ = recurrence.preview("FREQ=DAILY;BYHOUR=9;BYMINUTE=0", "Asia/Jerusalem", at(JLM, 2026, 3, 25, 12, 0), 3)
    assert [(o["local"], o["dst_shift"]) for o in occ] == [
        ("2026-03-26T09:00+02:00", False), ("2026-03-27T09:00+03:00", True), ("2026-03-28T09:00+03:00", False)]


# -- round minutes and the monthly cadence --------------------------------------------------------

@pytest.mark.parametrize("tz", ["Asia/Jerusalem", "America/New_York", "Australia/Lord_Howe", "UTC"])
@pytest.mark.parametrize("rule", [
    "FREQ=DAILY", "FREQ=HOURLY;INTERVAL=5", "FREQ=MINUTELY;INTERVAL=7", "FREQ=DAILY;BYHOUR=2;BYMINUTE=30",
    "FREQ=WEEKLY;BYDAY=MO,TH;BYHOUR=9", "FREQ=MONTHLY;BYDAY=TH;BYSETPOS=-1;BYHOUR=1;BYMINUTE=30",
    "FREQ=HOURLY;BYMINUTE=0,45",
])
def test_every_occurrence_is_on_a_round_minute_across_dst(rule, tz):
    # A start mid-minute, then a year that crosses both DST changes (Lord Howe shifts by 30 minutes).
    start = at(ZoneInfo(tz), 2026, 1, 10, 14, 37) + 23.75
    s = recurrence.Schedule(rule, tz, start)
    times = s.upcoming(start, 400) if "MINUTELY" not in rule else s.upcoming(start + 300 * DAY, 400)
    assert times and all(t % 60 == 0 for t in times), [t for t in times if t % 60][:3]
    assert all(datetime.fromtimestamp(t, ZoneInfo(tz)).second == 0 for t in times)
    assert times[0] > start  # only after the real start, though DTSTART is floored to 14:37:00


def test_plain_intervals_count_from_the_start_minute():
    start = at(JLM, 2026, 11, 26, 10, 15) + 42
    s = make_active({"a": (0, 0), "b": (5, 5)}, now=start, ap={"rrule": "FREQ=HOURLY;INTERVAL=6"})
    first = game.next_ap_tick_at(s)
    assert first == at(JLM, 2026, 11, 26, 16, 15) and first - start == 6 * 3600 - 42


def test_secondly_is_only_for_debug_fast_ticks():
    with pytest.raises(ConfigError, match="FREQ=SECONDLY is not allowed"):
        GameConfig.parse({"ap": {"rrule": "FREQ=SECONDLY;INTERVAL=120"}})
    # Fast ticks keep second-level timing from the exact start.
    s = make_active({"a": (0, 0), "b": (5, 5)}, now=T0 + 0.5, debug={"fast_ticks": True, "tick_seconds": 7})
    assert game.next_ap_tick_at(s) == T0 + 7.5


def test_time_limit_is_rounded_up_to_a_round_minute():
    start = at(JLM, 2026, 11, 26, 12, 0) + 30  # 12:00:30
    s = make_active({"a": (0, 0), "b": (5, 5)}, now=start, ap={"rrule": "FREQ=MINUTELY"}, max_duration_seconds=100)
    assert local(game.game_ends_at(s), JLM) == "2026-11-26T12:03:00+02:00"  # 12:02:10, rounded up
    entries = game.advance_time(s, start + 10 * 60, random.Random(0))
    # Ticks at 12:01 and 12:02; none at 12:03, the deadline itself (NOTES.md 7).
    assert [(e.event_type, local(e.ts, JLM)[11:19]) for e in entries] == [
        ("ap_tick", "12:01:00"), ("ap_tick", "12:02:00"), ("game_over", "12:03:00")]
    # Already on a round minute: unchanged. Debug fast ticks: exact.
    s = make_active({"a": (0, 0), "b": (5, 5)}, max_duration_seconds=120)
    assert game.game_ends_at(s) == T0 + 120
    s = make_active({"a": (0, 0), "b": (5, 5)}, now=T0 + 0.5, debug={"fast_ticks": True}, max_duration_seconds=100)
    assert game.game_ends_at(s) == T0 + 100.5


@pytest.mark.parametrize("rule", ["FREQ=MONTHLY;BYDAY=TH;BYSETPOS=-1", "FREQ=MONTHLY;BYDAY=-1TH;BYHOUR=9",
                                  "FREQ=MONTHLY;BYDAY=TH;BYSETPOS=4", "FREQ=MONTHLY;BYMONTHDAY=-1"])
def test_monthly_weekday_rules_may_be_35_days_apart(rule):
    stats = recurrence.rule_stats(rule, "Asia/Jerusalem")
    assert stats.max_gap > 31 * DAY and stats.max_gap <= recurrence.MAX_GAP_SECONDS
    GameConfig.parse({"ap": {"rrule": rule, "timezone": "Asia/Jerusalem"}})
    # The last Thursday of 2026: 26 Nov, then 31 Dec, 35 days later.
    if "SETPOS=-1" in rule:
        s = recurrence.Schedule(rule, "UTC", datetime(2026, 11, 26, 12, tzinfo=ZoneInfo("UTC")).timestamp())
        a, b = s.upcoming(s.dtstart - DAY, 2)
        assert b - a == 35 * DAY

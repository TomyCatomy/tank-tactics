"""Expanding a rule for validation, under a time budget (dateutil has no search bound
for rules that never match)."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .rules import RecurrenceError, compile_rule
from .zones import epoch

VALIDATION_DEADLINE = 0.5  # seconds; dateutil can search for a very long time for rules that never fire
SAMPLE_SIZE = 3000
SAMPLE_MONTHS = 26  # sample this far ahead, so at least 24 whole calendar months are checked


def sample(rule: str, zone: ZoneInfo, ref: datetime) -> tuple[list[float], bool]:
    """Occurrence epochs within ``SAMPLE_MONTHS`` of ``ref`` (at most ``SAMPLE_SIZE``),
    under a time budget, and whether they reach that horizon (False: the sample ran out first)."""
    horizon = ref + timedelta(days=SAMPLE_MONTHS * 31)
    out: list[datetime] = []
    done = threading.Event()
    full = [True]

    def work() -> None:
        try:
            r = compile_rule(rule, ref.replace(second=0, microsecond=0))
            for w in r.xafter(ref, inc=False):
                if w > horizon:
                    break
                if len(out) >= SAMPLE_SIZE:
                    full[0] = False
                    break
                out.append(w)
        except Exception:  # noqa: BLE001 - dateutil may reject a combination; treated as "never fires"
            out.clear()
        finally:
            done.set()

    # dateutil has no search bound for rules that never match, so run it under a
    # deadline. A thread that overruns finishes on its own (at most a few seconds).
    t = threading.Thread(target=work, daemon=True)
    t.start()
    if not done.wait(VALIDATION_DEADLINE):
        raise RecurrenceError("never fires, or is too complex to check")
    return sorted({epoch(w, zone) for w in out}), full[0]

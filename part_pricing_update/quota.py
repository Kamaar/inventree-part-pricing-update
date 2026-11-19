"""API quota helpers (independent from Django).

The daily quota of each supplier is counted per UTC day, and spread over
the scheduled runs left in the day, so that it is not used up in the
first hours of the day.
"""

import math
from datetime import datetime, timedelta, timezone

# Official limits of the default API plans
OFFICIAL_LIMITS = {
    'mouser': {'per_day': 1000, 'per_minute': 30},
    'digikey': {'per_day': 1000, 'per_minute': 120},
    'farnell': {'per_day': 1000, 'per_minute': 120},  # 2 per second
}


def next_utc_midnight(now: datetime) -> datetime:
    """Start of the next UTC day, when the daily counters are reset."""
    now = now.astimezone(timezone.utc)
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1)


def runs_left_today(now: datetime, interval_minutes: int) -> int:
    """Number of scheduled runs left before the daily counters are reset (current run included)."""
    seconds = (next_utc_midnight(now) - now).total_seconds()
    return max(1, math.ceil(seconds / (max(interval_minutes, 1) * 60)))


def run_allowance(remaining: int, now: datetime, interval_minutes: int, cap: int) -> int:
    """Requests that a single scheduled run may use for one supplier.

    Arguments:
        remaining: requests left today for the supplier
        now: current time (timezone aware)
        interval_minutes: interval between the scheduled runs
        cap: maximum requests per run (from the plugin settings)
    """
    if remaining <= 0:
        return 0

    share = math.ceil(remaining / runs_left_today(now, interval_minutes))
    return max(0, min(share, cap, remaining))


def interleave(groups: list[list]) -> list:
    """Round-robin merge of the lists: a, b, c, a, b, c, ...

    The requests to different suppliers alternate, so that the minimum delay
    between two requests to the same API overlaps with the other requests.
    """
    result = []

    for i in range(max((len(g) for g in groups), default=0)):
        result.extend(g[i] for g in groups if i < len(g))

    return result

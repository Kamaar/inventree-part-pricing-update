"""Tests for the API quota spreading."""

from datetime import datetime, timezone

from part_pricing_update.quota import interleave, next_utc_midnight, run_allowance, runs_left_today


def utc(hour, minute=0):
    return datetime(2026, 10, 28, hour, minute, tzinfo=timezone.utc)


def test_runs_left_today():
    assert runs_left_today(utc(0), 15) == 96
    assert runs_left_today(utc(23, 50), 15) == 1
    assert next_utc_midnight(utc(12)) == datetime(2026, 10, 29, tzinfo=timezone.utc)


def test_allowance_spreads_quota_over_the_day():
    # 800 requests at midnight, 96 runs: 9 per run
    assert run_allowance(800, utc(0), 15, cap=20) == 9

    # Following the allowance for a whole day never exceeds the quota
    remaining = 800
    for run in range(96):
        remaining -= run_allowance(remaining, utc(run // 4, (run % 4) * 15), 15, cap=20)
    assert 0 <= remaining < 800


def test_allowance_limits():
    assert run_allowance(0, utc(10), 15, cap=20) == 0
    assert run_allowance(500, utc(23, 50), 15, cap=20) == 20  # capped
    assert run_allowance(3, utc(23, 50), 15, cap=20) == 3  # never above the remaining requests


def test_interleave():
    assert interleave([[1, 2, 3], ['a'], ['x', 'y']]) == [1, 'a', 'x', 2, 'y', 3]
    assert interleave([]) == []

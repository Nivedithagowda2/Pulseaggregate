from datetime import datetime, timezone

import pytest

from pulse import rules
from pulse.metrics import Metrics, percentile

UTC = timezone.utc


def dt(*a):
    return datetime(*a, tzinfo=UTC)


def test_floor_and_ceil_hour():
    t = dt(2026, 10, 31, 10, 17, 5)
    assert rules.floor_hour(t) == dt(2026, 10, 31, 10)
    assert rules.ceil_hour(t) == dt(2026, 10, 31, 11)
    assert rules.ceil_hour(dt(2026, 10, 31, 10)) == dt(2026, 10, 31, 10)  # already aligned


def test_floor_and_ceil_day():
    t = dt(2026, 10, 31, 23, 59)
    assert rules.floor_day(t) == dt(2026, 10, 31)
    assert rules.ceil_day(t) == dt(2026, 11, 1)
    assert rules.ceil_day(dt(2026, 10, 31)) == dt(2026, 10, 31)


def test_parse_ts_variants():
    assert rules.parse_ts("2026-10-31") == dt(2026, 10, 31)
    assert rules.parse_ts("2026-10-31T10:30:00Z") == dt(2026, 10, 31, 10, 30)
    assert rules.parse_ts("2026-10-31T10:30:00+05:30") == dt(2026, 10, 31, 5, 0)
    assert rules.parse_ts(None) is None and rules.parse_ts("  ") is None
    with pytest.raises(ValueError):
        rules.parse_ts("not-a-date")


def test_resolve_range_defaults_and_alignment():
    now = dt(2026, 10, 31, 10, 17)
    start, end = rules.resolve_range(None, None, "hour", 2, now)
    assert end == dt(2026, 10, 31, 11) and start == dt(2026, 10, 29, 10)
    start, end = rules.resolve_range(None, None, "day", 7, now)
    assert end == dt(2026, 11, 1) and start == dt(2026, 10, 24)


def test_resolve_range_rejects_backwards_range():
    with pytest.raises(ValueError):
        rules.resolve_range(dt(2026, 10, 31), dt(2026, 10, 30), "day", 7)


def test_business_rules_shape():
    assert set(rules.VALID_STATUSES) < set(rules.STATUSES)
    assert "cancelled" not in rules.VALID_STATUSES


def test_percentile_nearest_rank():
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 50) == 5
    assert percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert percentile([], 50) == 0.0


def test_metrics_snapshot():
    m = Metrics()
    for v in (10, 20, 30):
        m.observe("GET /x", v)
    m.incr("cross_tenant_blocked")
    snap = m.snapshot()
    assert snap["latency"]["GET /x"]["count"] == 3
    assert snap["counters"]["cross_tenant_blocked"] == 1

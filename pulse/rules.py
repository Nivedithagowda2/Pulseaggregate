"""
Business rules and time handling. THE single source of truth.

The aggregation worker, the API, the direct-SQL verification and the benchmarks all
import from here, so the summaries and the checker can never disagree about a rule.

Rules
-----
  status      counts in total_orders   counts in sales / valid_orders   in top-N products
  placed      yes                      yes                              yes
  shipped     yes                      yes                              yes
  cancelled   yes                      no                               no

  total_orders     = every order created in the range (any status)
  valid_orders     = total_orders minus cancelled
  total_sales      = sum(amount) of valid orders
  cancelled_orders = total_orders - valid_orders

When an order moves placed -> cancelled: placed count -1, cancelled count +1,
valid_orders -1, total_sales -amount, total_orders unchanged.

Time
----
Everything is UTC. Orders are bucketed by created_at. Ranges are aligned to whole
buckets (hour or day): `from` is rounded down, `to` is rounded up, and the range is
[start, end). The API echoes the effective range in every response.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

UTC = timezone.utc

STATUSES = ("placed", "shipped", "cancelled")
VALID_STATUSES = ("placed", "shipped")

# Default look-back (days) for each dashboard query when the caller gives no range.
DEFAULT_DAYS = {
    "count": 7,
    "trends_hour": 2,
    "trends_day": 30,
    "top": 7,
    "breakdown": 30,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def floor_hour(dt: datetime) -> datetime:
    return to_utc(dt).replace(minute=0, second=0, microsecond=0)


def ceil_hour(dt: datetime) -> datetime:
    dt = to_utc(dt)
    floored = floor_hour(dt)
    return floored if floored == dt else floored + timedelta(hours=1)


def floor_day(dt: datetime) -> datetime:
    return to_utc(dt).replace(hour=0, minute=0, second=0, microsecond=0)


def ceil_day(dt: datetime) -> datetime:
    dt = to_utc(dt)
    floored = floor_day(dt)
    return floored if floored == dt else floored + timedelta(days=1)


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse 'YYYY-MM-DD' or an ISO-8601 datetime. Naive values are treated as UTC."""
    if value is None or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return to_utc(datetime.fromisoformat(text))
    except ValueError:
        raise ValueError(f"Invalid date/time '{value}'. Use YYYY-MM-DD or ISO-8601.")


def resolve_range(
    start: Optional[datetime],
    end: Optional[datetime],
    unit: str,
    default_days: int,
    now: Optional[datetime] = None,
) -> Tuple[datetime, datetime]:
    """Apply defaults and align to whole buckets. Returns [start, end)."""
    if unit not in ("hour", "day"):
        raise ValueError("unit must be 'hour' or 'day'")
    now = now or utcnow()
    end = end or now
    start = start or (end - timedelta(days=default_days))
    if start >= end:
        raise ValueError("'from' must be earlier than 'to'")
    if unit == "hour":
        return floor_hour(start), ceil_hour(end)
    return floor_day(start), ceil_day(end)

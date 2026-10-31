"""
The five standard dashboard queries by name, with their default ranges.

Shared by the correctness checker (guard.py) and the benchmarks so they always
exercise exactly the same queries the API serves.
"""
from . import direct, queries
from .rules import DEFAULT_DAYS, resolve_range

QUERY_NAMES = ["count", "trends_hour", "trends_day", "top", "breakdown"]

# name -> (HTTP path suffix, extra query params)
HTTP_PATHS = {
    "count": ("orders/count", {}),
    "trends_hour": ("orders/trends", {"granularity": "hour"}),
    "trends_day": ("orders/trends", {"granularity": "day"}),
    "top": ("products/top", {"limit": 10}),
    "breakdown": ("orders/breakdown", {}),
}

_UNITS = {"count": "hour", "trends_hour": "hour", "trends_day": "day", "top": "day", "breakdown": "hour"}


def effective_range(name, now=None):
    return resolve_range(None, None, _UNITS[name], DEFAULT_DAYS[name], now)


def _run(module, conn, name, tenant_id, now=None):
    start, end = effective_range(name, now)
    if name == "count":
        return module.totals(conn, tenant_id, start, end)
    if name == "trends_hour":
        return module.trends(conn, tenant_id, start, end, "hour")
    if name == "trends_day":
        return module.trends(conn, tenant_id, start, end, "day")
    if name == "top":
        return module.top_products(conn, tenant_id, start, end, 10)
    if name == "breakdown":
        return module.breakdown(conn, tenant_id, start, end)
    raise ValueError(f"unknown query {name}")


def run_summary(conn, name, tenant_id, now=None):
    return _run(queries, conn, name, tenant_id, now)


def run_direct(conn, name, tenant_id, now=None):
    return _run(direct, conn, name, tenant_id, now)

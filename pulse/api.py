
"""
PulseAggregate REST API.

Tenant endpoints (header: X-API-Key: <tenant key>):
  GET /tenants/{tenant_id}/orders/count      ?from=&to=
  GET /tenants/{tenant_id}/orders/trends     ?from=&to=&granularity=hour|day
  GET /tenants/{tenant_id}/products/top      ?from=&to=&limit=10
  GET /tenants/{tenant_id}/orders/breakdown  ?from=&to=

Admin / PulseGuard endpoints (header: X-Admin-Key):
  GET  /admin/status
  GET  /admin/verify/{tenant_id}
  POST /admin/verify-and-repair/{tenant_id}
  POST /admin/rebuild/{tenant_id}
"""
import os
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
import hmac
import time
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
)
from fastapi.middleware.cors import CORSMiddleware

from . import auth, guard, queries
from .aggregator import rebuild_tenant
from .config import settings
from .db import connection
from .limits import TenantLimiter
from .metrics import Metrics
from .rules import DEFAULT_DAYS, parse_ts, resolve_range, to_utc, utcnow


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PulseAggregate",
    version="1.0.0",
    description="Fast, tenant-isolated dashboard analytics from pre-computed summaries."
)


# ---------------------------------------------------------------------------
# CORS: allow the local React frontend to communicate with FastAPI
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "X-API-Key",
        "X-Admin-Key",
        "Content-Type",
    ],
)


metrics = Metrics()
limiter = TenantLimiter(settings.max_concurrent_per_tenant)


# ---------------------------------------------------------------------------
# Helpers and request monitoring
# ---------------------------------------------------------------------------

@app.middleware("http")
async def time_requests(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)

    ms = (time.perf_counter() - t0) * 1000

    route = request.scope.get("route")
    name = getattr(route, "path", request.url.path)

    metrics.observe(f"{request.method} {name}", ms)

    response.headers["X-Response-Time-ms"] = f"{ms:.1f}"

    return response


def _clean(obj):
    """Make query results JSON friendly."""

    if isinstance(obj, Decimal):
        return float(obj)

    if isinstance(obj, datetime):
        return obj.isoformat()

    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]

    return obj


def _range(from_, to, unit, default_days):
    try:
        return resolve_range(
            parse_ts(from_),
            parse_ts(to),
            unit,
            default_days
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _meta(conn, start, end, query_ms):
    """Range actually used + how fresh the data is (bonus B-1)."""

    state = queries.freshness(conn)
    refreshed = state["last_refresh_at"]

    age = (
        round((utcnow() - to_utc(refreshed)).total_seconds(), 1)
        if refreshed
        else None
    )

    return {
        "effective_from": start.isoformat(),
        "effective_to": end.isoformat(),
        "data_updated_at": refreshed.isoformat() if refreshed else None,
        "data_age_seconds": age,
        "updated": (
            f"updated {age:.0f} seconds ago"
            if age is not None
            else "never refreshed"
        ),
        "query_ms": round(query_ms, 2),
    }


# ---------------------------------------------------------------------------
# Security and tenant authorization
# ---------------------------------------------------------------------------

def authorize_tenant(
    tenant_id: int,
    x_api_key: Optional[str] = Header(default=None)
) -> int:
    """The tenant in the URL must be the tenant that owns the API key."""

    if not x_api_key:
        metrics.incr("auth_missing_key")
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key header"
        )

    owner = auth.tenant_for_key(x_api_key)

    if owner is None:
        metrics.incr("auth_invalid_key")
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )

    if owner != tenant_id:
        metrics.incr("cross_tenant_blocked")
        raise HTTPException(
            status_code=403,
            detail="This API key cannot access that tenant"
        )

    return tenant_id


def tenant_slot(tenant_id: int = Depends(authorize_tenant)):
    """Per-tenant concurrency limit (B-3). Released when the response is done."""

    if not limiter.try_acquire(tenant_id):
        metrics.incr("rate_limited")
        raise HTTPException(
            status_code=429,
            detail="Too many concurrent requests for this tenant"
        )

    try:
        yield tenant_id
    finally:
        limiter.release(tenant_id)


def require_admin(
    x_admin_key: Optional[str] = Header(default=None)
):
    if (
        not x_admin_key
        or not hmac.compare_digest(x_admin_key, settings.admin_key)
    ):
        raise HTTPException(
            status_code=401,
            detail="Admin key required"
        )


# ---------------------------------------------------------------------------
# Tenant API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/tenants/{tenant_id}/orders/count")
def orders_count(
    tenant_id: int = Depends(tenant_slot),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
):
    start, end = _range(
        from_,
        to,
        "hour",
        DEFAULT_DAYS["count"]
    )

    with connection() as conn:
        t0 = time.perf_counter()

        data = queries.totals(conn, tenant_id, start, end)

        ms = (time.perf_counter() - t0) * 1000
        meta = _meta(conn, start, end, ms)

    return _clean({
        "tenant_id": tenant_id,
        **data,
        "meta": meta
    })


@app.get("/tenants/{tenant_id}/orders/trends")
def orders_trends(
    tenant_id: int = Depends(tenant_slot),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    granularity: str = Query(
        default="day",
        pattern="^(hour|day)$"
    ),
):
    default_days = DEFAULT_DAYS[
        "trends_hour" if granularity == "hour" else "trends_day"
    ]

    start, end = _range(
        from_,
        to,
        granularity,
        default_days
    )

    with connection() as conn:
        t0 = time.perf_counter()

        data = queries.trends(
            conn,
            tenant_id,
            start,
            end,
            granularity
        )

        ms = (time.perf_counter() - t0) * 1000
        meta = _meta(conn, start, end, ms)

    return _clean({
        "tenant_id": tenant_id,
        "granularity": granularity,
        "points": data,
        "meta": meta
    })


@app.get("/tenants/{tenant_id}/products/top")
def products_top(
    tenant_id: int = Depends(tenant_slot),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    limit: int = Query(default=10, ge=1, le=100),
):
    start, end = _range(
        from_,
        to,
        "day",
        DEFAULT_DAYS["top"]
    )

    with connection() as conn:
        t0 = time.perf_counter()

        data = queries.top_products(
            conn,
            tenant_id,
            start,
            end,
            limit
        )

        ms = (time.perf_counter() - t0) * 1000
        meta = _meta(conn, start, end, ms)

    return _clean({
        "tenant_id": tenant_id,
        "products": data,
        "meta": meta
    })


@app.get("/tenants/{tenant_id}/orders/breakdown")
def orders_breakdown(
    tenant_id: int = Depends(tenant_slot),
    from_: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
):
    start, end = _range(
        from_,
        to,
        "hour",
        DEFAULT_DAYS["breakdown"]
    )

    with connection() as conn:
        t0 = time.perf_counter()

        data = queries.breakdown(
            conn,
            tenant_id,
            start,
            end
        )

        ms = (time.perf_counter() - t0) * 1000
        meta = _meta(conn, start, end, ms)

    return _clean({
        "tenant_id": tenant_id,
        "by_status": data,
        "meta": meta
    })


# ---------------------------------------------------------------------------
# PulseGuard monitoring and administration
# ---------------------------------------------------------------------------

@app.get(
    "/admin/status",
    dependencies=[Depends(require_admin)]
)
def admin_status():

    with connection() as conn:
        state = queries.freshness(conn)
        watermark = state["watermark"]

        backlog = None

        if watermark is not None:
            backlog = conn.execute(
                "SELECT COUNT(*) AS n FROM orders WHERE updated_at > %s",
                (watermark,)
            ).fetchone()["n"]

        estimate = conn.execute(
            "SELECT reltuples::bigint AS n FROM pg_class WHERE relname = 'orders'"
        ).fetchone()

    refreshed = state["last_refresh_at"]

    age = (
        round((utcnow() - to_utc(refreshed)).total_seconds(), 1)
        if refreshed
        else None
    )

    snapshot = metrics.snapshot()

    return _clean({
        "freshness": {
            "last_refresh_at": refreshed,
            "data_age_seconds": age,
            "watermark": watermark,
            "last_cycle_ms": state["last_cycle_ms"],
            "rows_processed_total": state["rows_processed"],
            "backlog_changed_orders": backlog,
        },
        "orders_estimate": estimate["n"] if estimate else None,
        "api_latency": snapshot["latency"],
        "security_counters": snapshot["counters"],
    })


@app.get(
    "/admin/verify/{tenant_id}",
    dependencies=[Depends(require_admin)]
)
def admin_verify(tenant_id: int):
    with connection() as conn:
        return _clean(
            guard.verify_tenant(conn, tenant_id)
        )


@app.post(
    "/admin/verify-and-repair/{tenant_id}",
    dependencies=[Depends(require_admin)]
)
def admin_verify_and_repair(tenant_id: int):
    with connection() as conn:
        return _clean(
            guard.verify_and_repair(conn, tenant_id)
        )


@app.post(
    "/admin/rebuild/{tenant_id}",
    dependencies=[Depends(require_admin)]
)
def admin_rebuild(tenant_id: int):

    t0 = time.perf_counter()

    with connection() as conn:
        rebuild_tenant(conn, tenant_id)

    return {
        "tenant_id": tenant_id,
        "rebuilt": True,
        "duration_ms": round(
            (time.perf_counter() - t0) * 1000,
            1
        )
    }


# ============================================================
# PULSEAGGREGATE - AUTOMATIC REACT DASHBOARD INTEGRATION
# ============================================================

import json
from pathlib import Path

from fastapi import HTTPException


def _ui_load_keys():
    """
    Read tenant API keys from the same JSON file used by Streamlit.
    Keys stay on the backend and are never sent to React.
    """
    keys_file = Path(os.getenv("KEYS_FILE", "data/api_keys.json"))

    if not keys_file.exists():
        raise HTTPException(
            status_code=500,
            detail=f"API keys file not found: {keys_file}"
        )

    try:
        data = json.loads(keys_file.read_text(encoding="utf-8"))
        tenants = data.get("tenants", {})

        return {
            int(tenant_id): details["api_key"]
            for tenant_id, details in tenants.items()
            if isinstance(details, dict) and details.get("api_key")
        }

    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Could not read API keys file: {exc}"
        )


def _ui_check_tenant(tenant_id: int):
    """
    Confirm that the selected tenant exists and its key belongs
    to that tenant before returning data.
    """
    keys = _ui_load_keys()
    api_key = keys.get(tenant_id)

    if not api_key:
        raise HTTPException(status_code=404, detail="Store not found")

    authenticated_tenant = auth.tenant_for_key(api_key)

    if authenticated_tenant is None or int(authenticated_tenant) != tenant_id:
        raise HTTPException(
            status_code=403,
            detail="Store authentication failed"
        )

    return api_key


@app.get("/ui/stores")
def ui_get_stores():
    """Return store IDs only. Never return API keys."""
    keys = _ui_load_keys()
    return {"stores": sorted(keys.keys())}


@app.get("/ui/tenants/{tenant_id}/orders/count")
def ui_orders_count(tenant_id: int):
    _ui_check_tenant(tenant_id)
    return orders_count(tenant_id)


@app.get("/ui/tenants/{tenant_id}/orders/trends")
def ui_orders_trends(tenant_id: int, granularity: str = "day"):
    _ui_check_tenant(tenant_id)

    if granularity not in ("day", "hour"):
        raise HTTPException(
            status_code=400,
            detail="Granularity must be day or hour"
        )

    return orders_trends(tenant_id, granularity)


@app.get("/ui/tenants/{tenant_id}/products/top")
def ui_top_products(tenant_id: int, limit: int = 10):
    _ui_check_tenant(tenant_id)
    return top_products(tenant_id, limit)


@app.get("/ui/tenants/{tenant_id}/orders/breakdown")
def ui_orders_breakdown(tenant_id: int):
    _ui_check_tenant(tenant_id)
    return orders_breakdown(tenant_id)


@app.get("/ui/admin/status")
def ui_admin_status():
    return admin_status()


@app.get("/ui/admin/verify/{tenant_id}")
def ui_admin_verify(tenant_id: int):
    return admin_verify(tenant_id)


@app.post("/ui/admin/verify-and-repair/{tenant_id}")
def ui_admin_verify_repair(tenant_id: int):
    return admin_verify_and_repair(tenant_id)


@app.post("/ui/admin/rebuild/{tenant_id}")
def ui_admin_rebuild(tenant_id: int):
    return admin_rebuild(tenant_id)
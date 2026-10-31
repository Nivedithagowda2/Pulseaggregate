"""
Incremental aggregation.

Strategy: "recompute the affected buckets from the source", never "add/subtract deltas".

When orders change, we work out which (tenant, hour) and (tenant, day) buckets they
belong to, then delete and re-insert exactly those summary rows from the orders table
(inside one transaction). Because the buckets are rewritten, not incremented:
  * a status change (placed -> cancelled) is automatically correct,
  * re-processing the same order twice (retries, overlap window) cannot double count,
  * duplicates in a batch collapse into one bucket (set semantics).
"""
import logging
from datetime import datetime

from .rules import VALID_STATUSES, floor_hour, to_utc

log = logging.getLogger("pulse.aggregator")

# Worker batches and full rebuilds take this advisory lock inside their transaction so a
# rebuild can never be overwritten by a concurrent worker batch (and vice versa).
LOCK_KEY = 727001

_VALID = list(VALID_STATUSES)


def buckets_for(rows):
    """Map changed order rows to the summary buckets they affect."""
    hourly, daily = set(), set()
    for r in rows:
        created = to_utc(r["created_at"])
        hourly.add((r["tenant_id"], floor_hour(created)))
        daily.add((r["tenant_id"], created.date()))
    return hourly, daily


def recompute_hourly(conn, hourly):
    if not hourly:
        return
    tenants = [b[0] for b in hourly]
    hours = [b[1] for b in hourly]
    conn.execute(
        """
        DELETE FROM agg_orders_hourly a
        USING unnest(%s::int[], %s::timestamptz[]) AS b(tenant_id, hour)
        WHERE a.tenant_id = b.tenant_id AND a.hour = b.hour
        """,
        (tenants, hours),
    )
    conn.execute(
        """
        INSERT INTO agg_orders_hourly (tenant_id, hour, status, order_count, sales)
        SELECT o.tenant_id, b.hour, o.status, COUNT(*), COALESCE(SUM(o.amount), 0)
        FROM unnest(%s::int[], %s::timestamptz[]) AS b(tenant_id, hour)
        JOIN orders o
          ON o.tenant_id = b.tenant_id
         AND o.created_at >= b.hour
         AND o.created_at <  b.hour + interval '1 hour'
        GROUP BY o.tenant_id, b.hour, o.status
        """,
        (tenants, hours),
    )


def recompute_product_daily(conn, daily):
    if not daily:
        return
    tenants = [b[0] for b in daily]
    days = [b[1] for b in daily]
    conn.execute(
        """
        DELETE FROM agg_product_daily a
        USING unnest(%s::int[], %s::date[]) AS b(tenant_id, day)
        WHERE a.tenant_id = b.tenant_id AND a.day = b.day
        """,
        (tenants, days),
    )
    conn.execute(
        """
        INSERT INTO agg_product_daily (tenant_id, day, product_id, order_count, units, sales)
        SELECT o.tenant_id, b.day, o.product_id, COUNT(*), SUM(o.quantity), SUM(o.amount)
        FROM unnest(%s::int[], %s::date[]) AS b(tenant_id, day)
        JOIN orders o
          ON o.tenant_id = b.tenant_id
         AND o.created_at >= b.day::timestamptz
         AND o.created_at <  (b.day + 1)::timestamptz
        WHERE o.status = ANY(%s)
        GROUP BY o.tenant_id, b.day, o.product_id
        """,
        (tenants, days, _VALID),
    )


def apply_changes(conn, rows):
    """Refresh every summary bucket touched by `rows`. Call inside a transaction."""
    hourly, daily = buckets_for(rows)
    recompute_hourly(conn, hourly)
    recompute_product_daily(conn, daily)
    return len(hourly), len(daily)


def rebuild_tenant(conn, tenant_id):
    """Rebuild ALL summaries of one tenant from the orders table (bonus B-2)."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        conn.execute("DELETE FROM agg_orders_hourly WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            """
            INSERT INTO agg_orders_hourly (tenant_id, hour, status, order_count, sales)
            SELECT tenant_id, date_trunc('hour', created_at), status, COUNT(*), COALESCE(SUM(amount), 0)
            FROM orders WHERE tenant_id = %s
            GROUP BY 1, 2, 3
            """,
            (tenant_id,),
        )
        conn.execute("DELETE FROM agg_product_daily WHERE tenant_id = %s", (tenant_id,))
        conn.execute(
            """
            INSERT INTO agg_product_daily (tenant_id, day, product_id, order_count, units, sales)
            SELECT tenant_id, date_trunc('day', created_at)::date, product_id,
                   COUNT(*), SUM(quantity), SUM(amount)
            FROM orders WHERE tenant_id = %s AND status = ANY(%s)
            GROUP BY 1, 2, 3
            """,
            (tenant_id, _VALID),
        )


def rebuild_all(conn, progress=None):
    """
    Rebuild every tenant, then set the worker watermark.

    The watermark is read BEFORE rebuilding, so any change committed during the rebuild
    has updated_at >= watermark and is picked up by the worker afterwards.
    """
    top = conn.execute("SELECT max(updated_at) AS m FROM orders").fetchone()["m"]
    tenant_ids = [r["tenant_id"] for r in conn.execute("SELECT tenant_id FROM tenants ORDER BY tenant_id")]
    for i, tenant_id in enumerate(tenant_ids, 1):
        rebuild_tenant(conn, tenant_id)
        if progress:
            progress(i, len(tenant_ids), tenant_id)
    if top is not None:
        conn.execute(
            "UPDATE agg_state SET watermark = GREATEST(COALESCE(watermark, %s), %s) WHERE id = 1",
            (top, top),
        )
    log.info("rebuilt summaries for %d tenants", len(tenant_ids))
    return len(tenant_ids)

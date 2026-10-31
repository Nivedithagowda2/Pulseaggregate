"""Dashboard queries answered from the pre-computed summary tables (the fast path)."""
from .rules import STATUSES, VALID_STATUSES

_VALID = list(VALID_STATUSES)


def totals(conn, tenant_id, start, end):
    row = conn.execute(
        """
        SELECT COALESCE(SUM(order_count), 0)                                       AS total_orders,
               COALESCE(SUM(order_count) FILTER (WHERE status = ANY(%s)), 0)       AS valid_orders,
               COALESCE(SUM(sales)       FILTER (WHERE status = ANY(%s)), 0)       AS total_sales
        FROM agg_orders_hourly
        WHERE tenant_id = %s AND hour >= %s AND hour < %s
        """,
        (_VALID, _VALID, tenant_id, start, end),
    ).fetchone()
    total, valid = int(row["total_orders"]), int(row["valid_orders"])
    return {
        "total_orders": total,
        "valid_orders": valid,
        "cancelled_orders": total - valid,
        "total_sales": row["total_sales"],
    }


def trends(conn, tenant_id, start, end, unit):
    if unit not in ("hour", "day"):
        raise ValueError("unit must be 'hour' or 'day'")
    rows = conn.execute(
        f"""
        SELECT date_trunc('{unit}', hour)                                        AS bucket,
               SUM(order_count)                                                  AS orders,
               COALESCE(SUM(sales) FILTER (WHERE status = ANY(%s)), 0)           AS sales
        FROM agg_orders_hourly
        WHERE tenant_id = %s AND hour >= %s AND hour < %s
        GROUP BY 1 ORDER BY 1
        """,
        (_VALID, tenant_id, start, end),
    ).fetchall()
    return [{"bucket": r["bucket"], "orders": int(r["orders"]), "sales": r["sales"]} for r in rows]


def top_products(conn, tenant_id, start, end, limit):
    rows = conn.execute(
        """
        SELECT a.product_id, p.name, a.orders, a.units, a.sales
        FROM (
            SELECT product_id,
                   SUM(order_count) AS orders,
                   SUM(units)       AS units,
                   SUM(sales)       AS sales
            FROM agg_product_daily
            WHERE tenant_id = %s AND day >= %s AND day < %s
            GROUP BY product_id
            ORDER BY sales DESC, product_id
            LIMIT %s
        ) a
        LEFT JOIN products p ON p.product_id = a.product_id AND p.tenant_id = %s
        ORDER BY a.sales DESC, a.product_id
        """,
        (tenant_id, start.date(), end.date(), limit, tenant_id),
    ).fetchall()
    return [
        {
            "product_id": r["product_id"],
            "name": r["name"],
            "orders": int(r["orders"]),
            "units": int(r["units"]),
            "sales": r["sales"],
        }
        for r in rows
    ]


def breakdown(conn, tenant_id, start, end):
    rows = conn.execute(
        """
        SELECT status, SUM(order_count) AS orders, SUM(sales) AS amount
        FROM agg_orders_hourly
        WHERE tenant_id = %s AND hour >= %s AND hour < %s
        GROUP BY status
        """,
        (tenant_id, start, end),
    ).fetchall()
    by_status = {r["status"]: r for r in rows}
    result = []
    for status in STATUSES:
        r = by_status.get(status)
        result.append(
            {
                "status": status,
                "orders": int(r["orders"]) if r else 0,
                "amount": r["amount"] if r else 0,
            }
        )
    return result


def freshness(conn):
    return conn.execute(
        "SELECT watermark, last_refresh_at, last_cycle_ms, rows_processed FROM agg_state WHERE id = 1"
    ).fetchone()

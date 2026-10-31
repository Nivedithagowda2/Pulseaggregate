"""
The same dashboard queries, run straight against the orders table.

Used for (1) correctness verification and (2) the 'naive approach' in benchmarks.
Business rules come from rules.py, exactly like the summary-table queries.
"""
from .rules import STATUSES, VALID_STATUSES

_VALID = list(VALID_STATUSES)


def totals(conn, tenant_id, start, end):
    row = conn.execute(
        """
        SELECT COUNT(*)                                                      AS total_orders,
               COUNT(*) FILTER (WHERE status = ANY(%s))                      AS valid_orders,
               COALESCE(SUM(amount) FILTER (WHERE status = ANY(%s)), 0)      AS total_sales
        FROM orders
        WHERE tenant_id = %s AND created_at >= %s AND created_at < %s
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
        SELECT date_trunc('{unit}', created_at)                                AS bucket,
               COUNT(*)                                                        AS orders,
               COALESCE(SUM(amount) FILTER (WHERE status = ANY(%s)), 0)        AS sales
        FROM orders
        WHERE tenant_id = %s AND created_at >= %s AND created_at < %s
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
                   COUNT(*)      AS orders,
                   SUM(quantity) AS units,
                   SUM(amount)   AS sales
            FROM orders
            WHERE tenant_id = %s AND created_at >= %s AND created_at < %s
              AND status = ANY(%s)
            GROUP BY product_id
            ORDER BY sales DESC, product_id
            LIMIT %s
        ) a
        LEFT JOIN products p ON p.product_id = a.product_id AND p.tenant_id = %s
        ORDER BY a.sales DESC, a.product_id
        """,
        (tenant_id, start, end, _VALID, limit, tenant_id),
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
        SELECT status, COUNT(*) AS orders, SUM(amount) AS amount
        FROM orders
        WHERE tenant_id = %s AND created_at >= %s AND created_at < %s
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

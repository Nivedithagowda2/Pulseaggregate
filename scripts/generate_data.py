"""
Fallback data generator (use the organisers' generator when they give it to you).

  python -m scripts.generate_data --orders 1000000 --tenants 10 --reset     # small test
  python -m scripts.generate_data --orders 10000000 --tenants 100 --reset   # main run

Creates tenants (with API keys written to data/api_keys.json), products, customers and
orders with SKEWED tenant sizes (a few tenants are much bigger, like real life), builds
indexes, then performs the initial summary build (bonus B-2 rebuild) and sets the watermark.
"""
import argparse
import json
import secrets
import time
from pathlib import Path

from pulse.aggregator import rebuild_all
from pulse.auth import hash_key
from pulse.config import settings
from pulse.db import apply_sql_file, connect

from .common import CUSTOMERS_PER_TENANT, PRODUCTS_PER_TENANT

ORDERS_SQL = """
INSERT INTO orders (order_id, tenant_id, customer_id, product_id, quantity, amount, status, created_at, updated_at)
SELECT s.g,
       s.tenant_id,
       (s.tenant_id - 1) * %(cust)s::int + 1 + floor(s.r_cust * %(cust)s::int)::int,
       (s.tenant_id - 1) * %(prod)s::int + 1 + floor(s.r_prod * %(prod)s::int)::int,
       1 + floor(s.r_qty * 5)::int,
       round((10 + s.r_amt * 4990)::numeric, 2),
       CASE WHEN s.r_status < 0.15 THEN 'cancelled'
            WHEN s.r_status < 0.45 THEN 'placed'
            ELSE 'shipped' END,
       s.created_at,
       LEAST(clock_timestamp(), s.created_at + s.r_upd * interval '2 days')
FROM (
    SELECT g,
           1 + floor(%(tenants)s::int * power(random(), %(skew)s::float8))::int AS tenant_id,
           random() AS r_cust, random() AS r_prod, random() AS r_qty,
           random() AS r_amt,  random() AS r_status, random() AS r_upd,
           now() - random() * %(days)s::float8 * interval '1 day' AS created_at
    FROM generate_series(%(start)s::bigint, %(end)s::bigint) g
) s
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--orders", type=int, default=1_000_000)
    ap.add_argument("--tenants", type=int, default=10)
    ap.add_argument("--days", type=int, default=90, help="orders are spread over the last N days")
    ap.add_argument("--skew", type=float, default=2.5, help="higher = a few much bigger tenants")
    ap.add_argument("--chunk", type=int, default=1_000_000)
    ap.add_argument("--reset", action="store_true", help="wipe existing data first")
    args = ap.parse_args()

    t_start = time.time()
    with connect() as conn:
        apply_sql_file(conn, "schema.sql")
        existing = conn.execute("SELECT count(*) AS n FROM tenants").fetchone()["n"]
        if existing and not args.reset:
            raise SystemExit("Data already exists. Re-run with --reset to wipe and regenerate.")
        conn.execute(
            "TRUNCATE orders, products, customers, tenants, agg_orders_hourly, agg_product_daily, app_probe RESTART IDENTITY"
        )
        conn.execute("UPDATE agg_state SET watermark = NULL, last_refresh_at = NULL, last_cycle_ms = NULL, rows_processed = 0")
        conn.execute("DROP INDEX IF EXISTS orders_updated_idx")
        conn.execute("DROP INDEX IF EXISTS orders_tenant_created_idx")

        # Tenants + API keys
        keys = {}
        rows = []
        for tid in range(1, args.tenants + 1):
            key = secrets.token_urlsafe(24)
            keys[str(tid)] = {"name": f"Store {tid}", "api_key": key}
            rows.append((tid, f"Store {tid}", hash_key(key)))
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO tenants (tenant_id, name, api_key_hash) VALUES (%s, %s, %s)", rows)
        out = Path(settings.keys_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"tenants": keys}, indent=2))
        print(f"wrote {len(keys)} tenant API keys to {out}")

        # Products and customers
        conn.execute(
            "INSERT INTO products (product_id, tenant_id, name) "
            "SELECT g, ((g - 1) / %s::int) + 1, 'Product ' || g FROM generate_series(1, %s::int) g",
            (PRODUCTS_PER_TENANT, args.tenants * PRODUCTS_PER_TENANT),
        )
        conn.execute(
            "INSERT INTO customers (customer_id, tenant_id, name) "
            "SELECT g, ((g - 1) / %s::int) + 1, 'Customer ' || g FROM generate_series(1, %s::int) g",
            (CUSTOMERS_PER_TENANT, args.tenants * CUSTOMERS_PER_TENANT),
        )

        # Orders, in chunks
        done = 0
        while done < args.orders:
            end = min(done + args.chunk, args.orders)
            conn.execute(
                ORDERS_SQL,
                {"tenants": args.tenants, "skew": args.skew, "days": args.days,
                 "cust": CUSTOMERS_PER_TENANT, "prod": PRODUCTS_PER_TENANT,
                 "start": done + 1, "end": end},
            )
            done = end
            print(f"  orders loaded: {done:,} / {args.orders:,}")

        conn.execute("SELECT setval('orders_order_id_seq', (SELECT max(order_id) FROM orders))")
        print("building indexes ...")
        apply_sql_file(conn, "indexes.sql")
        conn.execute("ANALYZE")

        print("building initial summaries ...")
        rebuild_all(conn, progress=lambda i, n, t: print(f"  tenant {t} ({i}/{n})") if i % 10 == 0 or i == n else None)
        conn.execute("ANALYZE")

        top = conn.execute(
            "SELECT tenant_id, count(*) AS n FROM orders GROUP BY 1 ORDER BY n DESC LIMIT 3"
        ).fetchall()
        print("largest tenants:", ", ".join(f"#{r['tenant_id']}={r['n']:,}" for r in top))
    print(f"done in {time.time() - t_start:.0f}s")


if __name__ == "__main__":
    main()

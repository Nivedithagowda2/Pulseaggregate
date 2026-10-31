"""
Simulate the main application writing to the orders table.

  python -m scripts.simulate_changes --tenant 5 --new 3 --cancel 2 --ship 2
  python -m scripts.simulate_changes --loop --rate 20          # continuous traffic, ~20 changes/sec
"""
import argparse
import random
import time

from pulse.db import connect

from .common import CUSTOMERS_PER_TENANT, PRODUCTS_PER_TENANT


def insert_orders(conn, tenant_id, n):
    ids = []
    for _ in range(n):
        row = conn.execute(
            """
            INSERT INTO orders (tenant_id, customer_id, product_id, quantity, amount, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, 'placed', now(), now()) RETURNING order_id
            """,
            (
                tenant_id,
                (tenant_id - 1) * CUSTOMERS_PER_TENANT + random.randint(1, CUSTOMERS_PER_TENANT),
                (tenant_id - 1) * PRODUCTS_PER_TENANT + random.randint(1, PRODUCTS_PER_TENANT),
                random.randint(1, 5),
                round(random.uniform(10, 5000), 2),
            ),
        ).fetchone()
        ids.append(row["order_id"])
    return ids


def change_status(conn, tenant_id, old, new, n):
    rows = conn.execute(
        """
        UPDATE orders SET status = %s, updated_at = now()
        WHERE order_id IN (
            SELECT order_id FROM orders
            WHERE tenant_id = %s AND status = %s AND created_at > now() - interval '7 days'
            ORDER BY random() LIMIT %s)
        RETURNING order_id
        """,
        (new, tenant_id, old, n),
    ).fetchall()
    return [r["order_id"] for r in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, help="default: random tenant each time")
    ap.add_argument("--new", type=int, default=0, help="insert N new 'placed' orders")
    ap.add_argument("--cancel", type=int, default=0, help="cancel N placed orders")
    ap.add_argument("--ship", type=int, default=0, help="ship N placed orders")
    ap.add_argument("--loop", action="store_true", help="run continuously")
    ap.add_argument("--rate", type=float, default=10.0, help="changes per second with --loop")
    args = ap.parse_args()

    with connect() as conn:
        tenants = [r["tenant_id"] for r in conn.execute("SELECT tenant_id FROM tenants ORDER BY 1")]
        if not tenants:
            raise SystemExit("no tenants found; run scripts/generate_data.py first")
        if args.loop:
            print(f"simulating ~{args.rate:.0f} changes/sec, Ctrl+C to stop")
            n = 0
            try:
                while True:
                    tid = args.tenant or random.choice(tenants)
                    roll = random.random()
                    if roll < 0.6:
                        insert_orders(conn, tid, 1)
                    elif roll < 0.8:
                        change_status(conn, tid, "placed", "shipped", 1)
                    else:
                        change_status(conn, tid, "placed", "cancelled", 1)
                    n += 1
                    if n % 100 == 0:
                        print(f"  {n} changes")
                    time.sleep(1.0 / args.rate)
            except KeyboardInterrupt:
                print(f"stopped after {n} changes")
            return
        tid = args.tenant or random.choice(tenants)
        if args.new:
            print(f"tenant {tid}: inserted orders {insert_orders(conn, tid, args.new)}")
        if args.cancel:
            print(f"tenant {tid}: cancelled orders {change_status(conn, tid, 'placed', 'cancelled', args.cancel)}")
        if args.ship:
            print(f"tenant {tid}: shipped orders {change_status(conn, tid, 'placed', 'shipped', args.ship)}")


if __name__ == "__main__":
    main()

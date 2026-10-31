"""Deliberately corrupt one tenant's summary to demo PulseGuard self-repair.

  python -m scripts.corrupt_summary --tenant 5
  python -m scripts.check_correctness --tenant 5            # shows MISMATCH
  python -m scripts.check_correctness --tenant 5 --repair   # shows REPAIRED
"""
import argparse

from pulse.db import connect


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", type=int, required=True)
    args = ap.parse_args()
    with connect() as conn:
        res = conn.execute(
            """
            UPDATE agg_orders_hourly SET order_count = order_count + 7, sales = sales + 999.99
            WHERE (tenant_id, hour, status) IN (
                SELECT tenant_id, hour, status FROM agg_orders_hourly
                WHERE tenant_id = %s AND status = 'shipped'
                ORDER BY hour DESC LIMIT 1)
            """,
            (args.tenant,),
        )
        print(f"corrupted {res.rowcount} summary row(s) for tenant {args.tenant}")


if __name__ == "__main__":
    main()

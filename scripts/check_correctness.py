"""
Compare summary-table answers with direct database answers (PulseGuard checker).

  python -m scripts.check_correctness --sample 5
  python -m scripts.check_correctness --all
  python -m scripts.check_correctness --tenant 3 --tenant 8 --repair

Exit code is 1 if any mismatch remains. Tip: run it when the worker has caught up
(or right after `python -m pulse.worker --once`), otherwise in-flight changes can
show as a transient mismatch.
"""
import argparse
import sys

from pulse.db import connect
from pulse.guard import verify_and_repair, verify_tenant


def pick_sample(conn, n):
    rows = conn.execute(
        "SELECT tenant_id, SUM(order_count) AS c FROM agg_orders_hourly GROUP BY 1 ORDER BY c DESC"
    ).fetchall()
    ids = [r["tenant_id"] for r in rows]
    if len(ids) <= n:
        return ids
    step = (len(ids) - 1) / max(n - 1, 1)
    return [ids[round(i * step)] for i in range(n)]  # biggest ... median ... smallest


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tenant", type=int, action="append", default=[])
    ap.add_argument("--sample", type=int, help="check N tenants spread from biggest to smallest")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--repair", action="store_true", help="rebuild a tenant's summaries if it mismatches")
    args = ap.parse_args()

    failures = 0
    with connect() as conn:
        tenants = list(args.tenant)
        if args.all:
            tenants = [r["tenant_id"] for r in conn.execute("SELECT tenant_id FROM tenants ORDER BY 1")]
        elif args.sample:
            tenants += pick_sample(conn, args.sample)
        if not tenants:
            ap.error("choose --tenant, --sample or --all")

        print(f"{'tenant':>7}  {'result':<10} {'ms':>8}  details")
        for tid in tenants:
            if args.repair:
                res = verify_and_repair(conn, tid)
                final = res.get("after", res["before"])
                status = "OK" if not res["repaired"] else ("REPAIRED" if res["ok"] else "STILL BAD")
                detail = f"mismatched before repair: {res['before']['failed']}" if res["repaired"] else ""
            else:
                final = verify_tenant(conn, tid)
                status = "OK" if final["ok"] else "MISMATCH"
                detail = f"mismatched: {final['failed']}" if not final["ok"] else ""
            if not final["ok"]:
                failures += 1
            print(f"{tid:>7}  {status:<10} {final['duration_ms']:>8}  {detail}")
    total = len(tenants)
    print(f"\n{total - failures}/{total} tenants match the direct database queries on all {5} dashboard queries")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()

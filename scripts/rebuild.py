"""Rebuild summaries from scratch (bonus B-2).

  python -m scripts.rebuild --all
  python -m scripts.rebuild --tenant 7
"""
import argparse
import time

from pulse.aggregator import rebuild_all, rebuild_tenant
from pulse.db import connect


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true")
    g.add_argument("--tenant", type=int)
    args = ap.parse_args()
    t0 = time.time()
    with connect() as conn:
        if args.all:
            rebuild_all(conn, progress=lambda i, n, t: print(f"  tenant {t} ({i}/{n})"))
        else:
            rebuild_tenant(conn, args.tenant)
    print(f"rebuild finished in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

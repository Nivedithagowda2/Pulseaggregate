"""
Background worker: keeps the summary tables fresh.

Every POLL_INTERVAL seconds it:
  1. reads the watermark (newest orders.updated_at fully processed),
  2. re-reads changed orders from (watermark - OVERLAP) using keyset pagination over
     (updated_at, order_id) -> uses the orders_updated_idx index, never a full scan,
  3. for each batch, in ONE transaction: recomputes the affected buckets and advances
     the watermark (so a crash can never advance it past unprocessed work),
  4. records a heartbeat (last_refresh_at) used for the "data age" shown to users.

The overlap window plus idempotent bucket recomputation means late-committing
transactions are not missed and retries cannot double count.
"""
import argparse
import logging
import signal
import time
from datetime import datetime, timedelta

from .aggregator import LOCK_KEY, apply_changes, rebuild_all
from .config import settings
from .db import apply_sql_file, connection
from .rules import UTC

log = logging.getLogger("pulse.worker")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_stop = False


def run_cycle(batch_size=None, overlap=None):
    """One polling cycle. Returns the number of changed orders processed."""
    batch_size = batch_size or settings.batch_size
    overlap = settings.overlap_seconds if overlap is None else overlap
    t0 = time.perf_counter()
    with connection() as conn:
        cycle_start = conn.execute("SELECT clock_timestamp() AS t").fetchone()["t"]
        watermark = conn.execute("SELECT watermark FROM agg_state WHERE id = 1").fetchone()["watermark"]
        if watermark is None:
            log.info("no watermark yet: bootstrapping summaries with a full rebuild")
            rebuild_all(conn)
            watermark = conn.execute("SELECT watermark FROM agg_state WHERE id = 1").fetchone()["watermark"]
        cursor_ts = (watermark or _EPOCH) - timedelta(seconds=overlap)
        cursor_id = 0
        processed = 0
        while True:
            rows = conn.execute(
                """
                SELECT order_id, tenant_id, product_id, created_at, updated_at
                FROM orders
                WHERE (updated_at, order_id) > (%s, %s)
                ORDER BY updated_at, order_id
                LIMIT %s
                """,
                (cursor_ts, cursor_id, batch_size),
            ).fetchall()
            if not rows:
                break
            newest = rows[-1]["updated_at"]
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                apply_changes(conn, rows)
                conn.execute(
                    """
                    UPDATE agg_state
                    SET watermark = GREATEST(COALESCE(watermark, %s), %s),
                        rows_processed = rows_processed + %s
                    WHERE id = 1
                    """,
                    (newest, newest, len(rows)),
                )
            cursor_ts, cursor_id = newest, rows[-1]["order_id"]
            processed += len(rows)
            if len(rows) < batch_size:
                break
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        conn.execute(
            "UPDATE agg_state SET last_refresh_at = %s, last_cycle_ms = %s WHERE id = 1",
            (cycle_start, elapsed_ms),
        )
    if processed:
        log.info("processed %d changed orders in %d ms", processed, elapsed_ms)
    return processed


def _handle_signal(signum, frame):
    global _stop
    _stop = True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="PulseAggregate background worker")
    parser.add_argument("--once", action="store_true", help="run a single cycle and exit")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    with connection() as conn:
        apply_sql_file(conn, "schema.sql")
        apply_sql_file(conn, "indexes.sql")

    if args.once:
        run_cycle()
        return

    log.info("worker started (poll every %.1fs, batch %d, overlap %.0fs)",
             settings.poll_interval, settings.batch_size, settings.overlap_seconds)
    while not _stop:
        try:
            run_cycle()
        except Exception:  # keep the worker alive; the next cycle retries safely
            log.exception("cycle failed; will retry")
        slept = 0.0
        while slept < settings.poll_interval and not _stop:
            time.sleep(0.25)
            slept += 0.25
    log.info("worker stopped")


if __name__ == "__main__":
    main()

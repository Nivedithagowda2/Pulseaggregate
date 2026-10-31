# PulseAggregate (with PulseGuard monitoring)

A multi-tenant aggregation layer that sits between the main database and store dashboards.
Dashboards read small **pre-computed summary tables** instead of scanning millions of orders,
so queries are fast, data stays close to live, and the main database is not hammered.

> **Status.** Written for Problem 1 (Fast Multi-Tenant Data Aggregation). Python modules compile and the
> pure logic (time rules, metrics) has unit tests that pass. The PostgreSQL paths (worker, SQL, API) were
> **not run against a live database** when this was written, so run the integration tests (section 9) and a
> 1M-row smoke test first. Fix anything they find before relying on benchmark numbers.

## 1. Architecture

```
 Main DB: orders (PostgreSQL)  --(app writes: insert / update status)-->
        |
        |  worker polls an index on (updated_at, order_id), every ~5 s, in batches
        v
 Worker: finds which (tenant, hour) and (tenant, day) buckets changed,
         DELETE + re-INSERT just those buckets from the source (one transaction)
        v
 Summary tables: agg_orders_hourly, agg_product_daily          agg_state (watermark, heartbeat)
        v
 FastAPI: X-API-Key -> tenant check -> read summaries  (never touches orders)
        v
 Streamlit dashboard + PulseGuard page         PulseGuard: verify vs direct SQL, rebuild, metrics
```

## 2. Quick start with Docker

```bash
docker compose up -d db
docker compose run --rm tools python -m scripts.generate_data --orders 1000000 --tenants 10 --reset
docker compose up -d --build            # worker + api + dashboard
```

* API docs: http://localhost:8000/docs
* Dashboard + PulseGuard: http://localhost:8501
* Tenant API keys are written to `data/api_keys.json` (generated, git-ignored).

Main run (10M rows, 100 tenants): `--orders 10000000 --tenants 100 --reset` (allow several minutes and ~4 GB disk).

## 3. Quick start without Docker

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://pulse:pulse@localhost:5432/pulse   # an existing empty database
export ADMIN_KEY=change-me-admin-key
python -m scripts.generate_data --orders 1000000 --tenants 10 --reset
python -m pulse.worker &                                   # background worker
uvicorn pulse.api:app --port 8000 &                        # API
streamlit run dashboard/app.py                             # optional UI
```

## 4. API

All tenant calls need `X-API-Key: <that tenant's key>`. A key only works for its own tenant (else 403).

```bash
KEY=$(python -c "import json;print(json.load(open('data/api_keys.json'))['tenants']['5']['api_key'])")
curl -H "X-API-Key: $KEY" "localhost:8000/tenants/5/orders/count?from=2026-10-24&to=2026-10-31"
curl -H "X-API-Key: $KEY" "localhost:8000/tenants/5/orders/trends?granularity=day"
curl -H "X-API-Key: $KEY" "localhost:8000/tenants/5/products/top?limit=10"
curl -H "X-API-Key: $KEY" "localhost:8000/tenants/5/orders/breakdown"
curl -H "X-API-Key: $KEY" "localhost:8000/tenants/6/orders/count"      # -> 403 (isolation)
```

| Endpoint | Requirement | Returns |
|---|---|---|
| `GET /tenants/{id}/orders/count?from&to` | FR-1 | total / valid / cancelled orders, total sales |
| `GET /tenants/{id}/orders/trends?from&to&granularity=hour\|day` | FR-2 | orders and sales per bucket |
| `GET /tenants/{id}/products/top?from&to&limit=10` | FR-3 | top-N products by sales |
| `GET /tenants/{id}/orders/breakdown?from&to` | FR-4 | orders and amount per status |

Defaults when `from`/`to` are omitted: count 7 days, trends 30 days (day) or 2 days (hour), top 7 days, breakdown 30 days.
`from`/`to` accept `YYYY-MM-DD` or ISO-8601, UTC. Ranges snap to whole hours (count, trends, breakdown) or days (top-N)
as `[from, to)`; the response echoes `effective_from` / `effective_to`. Every response has a `meta` block with
`"updated": "updated 12 seconds ago"` (B-1), `data_age_seconds` and `query_ms`.

PulseGuard (header `X-Admin-Key`): `GET /admin/status`, `GET /admin/verify/{tenant}`,
`POST /admin/verify-and-repair/{tenant}`, `POST /admin/rebuild/{tenant}`.

## 5. Business rules (one source of truth: `pulse/rules.py`)

| Status | in total_orders | in valid_orders / sales / top-N |
|---|---|---|
| placed | yes | yes |
| shipped | yes | yes |
| cancelled | yes | **no** |

Placed to cancelled: placed -1, cancelled +1, valid_orders -1, total_sales minus the order amount, total_orders unchanged.
The worker, API, checker and benchmarks all import these rules, so they cannot drift apart. **If the organisers define
sales differently, change `VALID_STATUSES` in `pulse/rules.py` only.**

## 6. How it stays correct and fresh

* **Change detection:** poll `orders.updated_at` through the index `(updated_at, order_id)` with keyset pagination. No triggers
  on the orders table (so normal writes are not slowed), no full-table scans.
* **Recompute, don't add deltas:** a changed order marks its hour/day bucket dirty; the worker rewrites that bucket from the
  source in one transaction. Status changes are automatically right, and retries cannot double count.
* **Watermark + overlap:** progress is saved in the same transaction as the batch; each cycle re-reads the last
  `OVERLAP_SECONDS` (default 10) so rows that commit late are not missed. Batches are deduplicated per bucket.
* **Rebuild vs worker:** both take the same advisory lock, so a rebuild cannot be overwritten by a concurrent batch.
* **Bootstrap:** with no watermark the worker does a full rebuild first. `scripts.generate_data` already does this.

## 7. Tenant security

* The tenant in the URL is never trusted alone: the API key must belong to that tenant (401 no/invalid key, 403 wrong tenant).
* Keys are stored only as SHA-256 hashes. All summary rows carry `tenant_id` and every query filters on it.
* Per-tenant concurrency limit (B-3): at most `MAX_CONCURRENT_PER_TENANT` (default 4) simultaneous requests, else 429.
* There is no response cache, so there is no cache-key leak (the INC-02 class of bug). If you add Redis, put `tenant_id` in every key.

## 8. PulseGuard: verify, rebuild, monitor

```bash
python -m scripts.check_correctness --sample 5          # biggest .. median .. smallest tenants
python -m scripts.check_correctness --all
python -m scripts.corrupt_summary --tenant 5            # demo: break a summary on purpose
python -m scripts.check_correctness --tenant 5          # MISMATCH
python -m scripts.check_correctness --tenant 5 --repair # REPAIRED (rebuilt from source, re-verified)
python -m scripts.rebuild --all                         # B-2: rebuild everything from scratch
python -m scripts.simulate_changes --tenant 5 --new 3 --cancel 2   # demo live updates
python -m scripts.simulate_changes --loop --rate 20                # continuous app traffic
```

Run the checker when the worker has caught up; in-flight changes can show as a transient mismatch.

## 9. Tests

```bash
pip install -r requirements.txt
pytest tests/test_rules.py                       # no database needed
createdb pulse_test                              # DISPOSABLE db: tests drop its public schema
TEST_DATABASE_URL=postgresql://pulse:pulse@localhost:5432/pulse_test pytest -v
```

Integration tests cover: summaries equal direct queries, new/updated orders flow through the worker, reprocessing is
idempotent, corruption is detected and repaired, 401/403 on every endpoint, API numbers equal database numbers, per-tenant limit.

## 10. Benchmarks (report measured numbers only)

Start the worker and API first.

```bash
python -m scripts.benchmark latency --reps 30                     # API vs raw SQL; biggest/median/smallest tenant
python -m scripts.benchmark impact --seconds 20 --load-procs 4 --include-direct
python -m scripts.benchmark freshness --runs 5
# after loading 1M rows, then again after 10M rows:
python -m scripts.benchmark compare benchmarks/latency_1000000.json benchmarks/latency_10000000.json
```

Targets (Good / Great): query < 1 s / < 200 ms; freshness < 5 min / < 30 s; normal app queries < 25% / < 10% slower with
dashboard load; numbers match on most / all queries; 10M vs 1M < 5x / < 2x slower. Results are saved to `benchmarks/*.json`.
Copy your measured results into this table before submitting:

| Metric | 1M rows | 10M rows | Target |
|---|---|---|---|
| Worst API p95 latency | | | < 200 ms |
| Freshness (new order visible) | | | < 30 s |
| App-query slowdown with dashboard load | | | < 10% |
| Queries matching direct SQL | | | all |
| Growth ratio 1M to 10M | | | < 2x |

## 11. Bonus items

B-1 freshness in every response, B-2 `scripts.rebuild`, B-3 per-tenant limit, B-5 PulseGuard page + `/admin/status`.
B-4 (add a metric from a config file) is not implemented.

## 12. Using the organisers' data

Needs these columns on the source table: `order_id, tenant_id, product_id, quantity, amount, status, created_at, updated_at`,
plus `products(product_id, tenant_id, name)` and a `tenants` table holding API-key hashes. If their schema differs, the
easiest fix is a view named `orders` that renames columns, or edit the SQL in `pulse/aggregator.py` and `pulse/direct.py`.
If their data has no `updated_at`, ask the organisers how updates are signalled before choosing another detection method.
Keep the `(updated_at, order_id)` and `(tenant_id, created_at)` indexes (`sql/indexes.sql`).

## 13. Known limitations

* Orders are bucketed by `created_at`; if `created_at`, `tenant_id` or `product_id` of an existing order is edited, the old bucket is not
  refreshed. Hard deletes are not detected either. Run `scripts.rebuild` after such changes.
* All times are UTC; "day" means a UTC day.
* The tenant filter is enforced in the API, not by Postgres row-level security. Test identities are API keys, not a login system.

## 14. Layout

```
pulse/       rules, aggregator (recompute buckets), worker, queries (fast path), direct (raw SQL), guard, api, auth, limits, metrics
scripts/     generate_data, simulate_changes, check_correctness, corrupt_summary, rebuild, benchmark
dashboard/   Streamlit UI + PulseGuard page
sql/         schema.sql, indexes.sql
tests/       unit + integration tests
```

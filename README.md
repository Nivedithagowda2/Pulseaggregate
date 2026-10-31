# PulseAggregate

**A multi-tenant aggregation layer that keeps store dashboards fast and fresh without overloading the main database.**

Built for the CyberForge hackathon by Mitigata, Problem 1: *Fast Multi-Tenant Data Aggregation*. Includes **PulseGuard**, a built-in module that verifies correctness, repairs summaries and monitors performance.

---

## Table of contents

1. [The problem](#1-the-problem)
2. [Our solution](#2-our-solution)
3. [Architecture](#3-architecture)
4. [How an update flows through the system](#4-how-an-update-flows-through-the-system)
5. [Features and requirement coverage](#5-features-and-requirement-coverage)
6. [Tech stack](#6-tech-stack)
7. [Project structure](#7-project-structure)
8. [Getting started](#8-getting-started)
9. [Using the API](#9-using-the-api)
10. [Business rules](#10-business-rules)
11. [Design decisions](#11-design-decisions)
12. [Tenant security](#12-tenant-security)
13. [PulseGuard: verify, repair, monitor](#13-pulseguard-verify-repair-monitor)
14. [Demo walkthrough](#14-demo-walkthrough)
15. [Performance results](#15-performance-results)
16. [Configuration](#16-configuration)
17. [Tests](#17-tests)
18. [Troubleshooting](#18-troubleshooting)
19. [Adapting to a different schema](#19-adapting-to-a-different-schema)
20. [Known limitations](#20-known-limitations)

---

## 1. The problem

Imagine an online store platform like Shopify. Each store owner is a **tenant**. Every owner wants a dashboard that answers:

- How many orders did I get, and how much did I sell?
- How are my sales changing day by day?
- Which are my top 10 products?
- How many orders are placed, shipped or cancelled?

With a few thousand records, a normal database query answers this instantly. With **millions of records** (the target is 10M orders across 100 tenants), the same query:

- **is slow**: it scans a lot of data, so dashboards take seconds or minutes;
- **hurts everyone**: heavy queries slow normal reads and writes, so one big store's report slows the app for all stores;
- **forces bad shortcuts**: a nightly job is cheap but shows yesterday's numbers, while querying live is fresh but expensive.

A 2023 real-world incident (INC-02) also shows the risk in the middle: a cache bug showed some users other users' data. Tenant separation has to be built in from the start.

## 2. Our solution

We build an **aggregation layer between the database and the dashboards**. Instead of counting millions of orders on every page load, the system keeps small **pre-computed summary tables** and refreshes only the parts that changed.

> Think of a restaurant kitchen: prepping ingredients before the rush is faster than cooking every dish from scratch when the order arrives.

| Goal | How we meet it |
|---|---|
| Fast | Dashboard queries read a few thousand small summary rows through a primary-key index, never the `orders` table. |
| Fresh | A background worker picks up changes every few seconds. |
| Safe for the database | Dashboards never query `orders`. Change detection uses an index instead of triggers or full scans. |
| Separate tenants | Per-tenant API keys, the tenant in the URL must match the key, and every query filters by `tenant_id`. |
| Correct | One shared rules file, summaries rebuilt from the source, and a checker that compares against direct database queries. |
| Scales | Summary size depends on the number of hours, days and products, not on the number of orders. |

## 3. Architecture

```mermaid
flowchart LR
    APP["Main application<br/>inserts and updates orders"] --> ORD[("orders<br/>PostgreSQL")]
    ORD -->|"changed rows<br/>(updated_at index)"| WK["Background worker"]
    WK -->|"recompute affected<br/>hour and day buckets"| SUM[("Summary tables<br/>agg_orders_hourly<br/>agg_product_daily")]
    SUM --> API["FastAPI<br/>API key per tenant"]
    API --> UI["Streamlit dashboard<br/>and PulseGuard page"]
    PG["PulseGuard"] -. "compare and repair" .-> SUM
    PG -. "direct SQL check" .-> ORD
```

**Components**

| Component | File(s) | Job |
|---|---|---|
| Source database | `sql/schema.sql` | `orders`, `products`, `customers`, `tenants` tables |
| Summary tables | `sql/schema.sql` | Pre-computed counts, sales, per-product sales |
| Worker | `pulse/worker.py`, `pulse/aggregator.py` | Detects changed orders and refreshes the affected summaries |
| Business rules | `pulse/rules.py` | One source of truth for what counts as sales, and time handling |
| API | `pulse/api.py`, `pulse/queries.py` | Four dashboard endpoints plus admin endpoints |
| Security | `pulse/auth.py`, `pulse/limits.py` | API-key tenant check and per-tenant concurrency limit |
| PulseGuard | `pulse/guard.py`, `pulse/direct.py` | Verifies summaries against raw SQL, repairs mismatches |
| Dashboard | `dashboard/app.py` | Streamlit store view and PulseGuard monitoring tab |
| Tools | `scripts/` | Data generator, order simulator, checker, rebuild, benchmarks |

## 4. How an update flows through the system

Say Store 5 has these numbers for one hour:

| | placed | shipped | cancelled | total orders | valid orders | sales (valid) |
|---|---|---|---|---|---|---|
| Before | 40 | 50 | 10 | 100 | 90 | 180,000 |

Then **2 new orders** (2,000 each) arrive and **5 placed orders** (2,000 each) are **cancelled** at the same time:

| | placed | shipped | cancelled | total orders | valid orders | sales (valid) |
|---|---|---|---|---|---|---|
| After | 37 | 50 | 15 | **102** | **87** | **174,000** |

What happens inside:

1. The application writes the changes, and each affected row gets a new `updated_at`.
2. Within a few seconds the worker asks for orders with `updated_at` newer than its saved **watermark** (minus a small overlap). It uses an index, so it never scans the whole table.
3. It works out which **(tenant, hour)** and **(tenant, day)** buckets those orders belong to.
4. In **one transaction** it deletes just those buckets and re-inserts them by recounting from the `orders` table, then saves the new watermark.
5. The next dashboard request reads the fresh summary rows. Because of the transaction, a reader sees the old state or the new state, never a half-updated one.

**The system does not calculate "+2 and -5".** It recounts the affected bucket from the real orders. This gives the same answer, and it cannot get a status change wrong or count a retried event twice.

## 5. Features and requirement coverage

**Must-have requirements**

| ID | Requirement | Status | Where |
|---|---|---|---|
| FR-1 | Counts and totals for a time range | Done | `GET /tenants/{id}/orders/count` |
| FR-2 | Trend over time (per hour or per day) | Done | `GET /tenants/{id}/orders/trends` |
| FR-3 | Top-N list | Done | `GET /tenants/{id}/products/top` |
| FR-4 | Breakdown by category (status) | Done | `GET /tenants/{id}/orders/breakdown` |
| FR-5 | Tenant filter on every query | Done | API key must match the tenant, 403 otherwise |
| FR-6 | New data shows up, including updates such as placed to cancelled | Done | Worker plus bucket recompute |
| FR-7 | Simple REST API with a short README | Done | FastAPI (`/docs`) and this file |

**Bonus items**

| ID | Bonus | Status |
|---|---|---|
| B-1 | Response shows how fresh the data is | Done (`"updated 12 seconds ago"` in every response) |
| B-2 | Rebuild all numbers from scratch | Done (`scripts.rebuild`, plus per-tenant repair in PulseGuard) |
| B-3 | Limit heavy queries per tenant | Done (concurrency limit per tenant, HTTP 429) |
| B-4 | Add a metric through a config file | Not implemented |
| B-5 | Monitoring page (query time, data lag) | Done (PulseGuard tab and `/admin/status`) |

## 6. Tech stack

| Layer | Technology |
|---|---|
| Database | PostgreSQL 16 |
| Worker and logic | Python 3 with psycopg 3 |
| API | FastAPI and Uvicorn |
| UI | Streamlit and pandas |
| Packaging | Docker Compose (optional) |
| Tests | pytest |

Redis and Kafka were not needed. Summary tables in PostgreSQL are durable, transactional and easy to verify.

## 7. Project structure

```
pulseaggregate/
├── pulse/                  core package
│   ├── rules.py            business rules and UTC time handling (single source of truth)
│   ├── aggregator.py       recompute buckets, full rebuild
│   ├── worker.py           polling loop with watermark and overlap
│   ├── queries.py          dashboard queries from summary tables (fast path)
│   ├── direct.py           the same queries on raw orders (verification, benchmarks)
│   ├── catalog.py          the five standard queries and their default ranges
│   ├── guard.py            PulseGuard: verify and repair
│   ├── api.py              FastAPI app
│   ├── auth.py             API key to tenant mapping
│   ├── limits.py           per-tenant concurrency limit
│   ├── metrics.py          in-process latency and counters
│   ├── config.py           settings from environment variables
│   └── db.py               connection pool, UTC sessions
├── scripts/
│   ├── generate_data.py    fallback data generator (skewed tenant sizes)
│   ├── simulate_changes.py insert, cancel and ship orders (live demo)
│   ├── check_correctness.py  compare summaries with raw SQL, optional repair
│   ├── corrupt_summary.py  break a summary on purpose (demo)
│   ├── rebuild.py          rebuild summaries from scratch
│   └── benchmark.py        latency, database impact, freshness, 1M vs 10M
├── dashboard/app.py        Streamlit UI and PulseGuard tab
├── sql/                    schema.sql, indexes.sql
├── tests/                  unit and integration tests
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## 8. Getting started

### Option A: Docker

```bash
docker compose up -d db
docker compose run --rm tools python -m scripts.generate_data --orders 1000000 --tenants 10 --reset
docker compose up -d --build
```

If your machine already runs PostgreSQL on port 5432, change `ports: ["5432:5432"]` for the `db` service in `docker-compose.yml` to `["5433:5432"]`.

### Option B: without Docker (Windows PowerShell)

Requirements: Python 3.10+ and PostgreSQL. Use your own PostgreSQL password.

```powershell
git clone https://github.com/Nivedithagowda2/Pulseaggregate.git
cd Pulseaggregate

python -m pip install -r requirements.txt

$env:DATABASE_URL = "postgresql://postgres:YOUR_PASSWORD@localhost:5432/postgres"
$env:ADMIN_KEY    = "change-me-admin-key"
$env:API_URL      = "http://127.0.0.1:8000"

# 1. Check the connection (must print: connected OK)
python -c "import os, psycopg; psycopg.connect(os.environ['DATABASE_URL']).close(); print('connected OK')"

# 2. Load data: 1 million orders, 10 tenants (this also writes data/api_keys.json)
python -m scripts.generate_data --orders 1000000 --tenants 10 --reset
```

Then start three programs, **each in its own terminal**. Every new terminal needs the three `$env:` lines first.

```powershell
python -m pulse.worker                          # terminal 1: background worker
python -m uvicorn pulse.api:app --port 8000     # terminal 2: API
python -m streamlit run dashboard/app.py        # terminal 3: dashboard
```

### Option C: without Docker (Linux or macOS)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/postgres
export ADMIN_KEY=change-me-admin-key
export API_URL=http://127.0.0.1:8000
python -m scripts.generate_data --orders 1000000 --tenants 10 --reset
python -m pulse.worker &
python -m uvicorn pulse.api:app --port 8000 &
python -m streamlit run dashboard/app.py
```

### Open the app

| What | URL |
|---|---|
| Dashboard and PulseGuard | http://localhost:8501 |
| API documentation | http://localhost:8000/docs |

The main run uses `--orders 10000000 --tenants 100 --reset` (allow several minutes and several GB of disk). Running `generate_data` wipes the data and **creates new API keys**, so restart the API and dashboard afterwards.

## 9. Using the API

Every tenant call needs the header `X-API-Key: <that tenant's key>`. Keys are written to `data/api_keys.json` by the data generator. A key only works for its own tenant.

```bash
curl -H "X-API-Key: $KEY" "http://127.0.0.1:8000/tenants/5/orders/count?from=2026-10-24&to=2026-10-31"
curl -H "X-API-Key: $KEY" "http://127.0.0.1:8000/tenants/5/orders/trends?granularity=day"
curl -H "X-API-Key: $KEY" "http://127.0.0.1:8000/tenants/5/products/top?limit=10"
curl -H "X-API-Key: $KEY" "http://127.0.0.1:8000/tenants/5/orders/breakdown"
curl -H "X-API-Key: $KEY" "http://127.0.0.1:8000/tenants/6/orders/count"   # 403: key belongs to tenant 5
```

| Endpoint | Returns |
|---|---|
| `GET /tenants/{id}/orders/count?from&to` | total, valid and cancelled orders, total sales |
| `GET /tenants/{id}/orders/trends?from&to&granularity=hour\|day` | orders and sales per bucket |
| `GET /tenants/{id}/products/top?from&to&limit=10` | top-N products by sales |
| `GET /tenants/{id}/orders/breakdown?from&to` | orders and amount per status |

**Dates.** `from` and `to` accept `YYYY-MM-DD` or ISO-8601, in UTC. Ranges snap to whole hours (count, trends, breakdown) or whole days (top-N) as `[from, to)`, and the response echoes `effective_from` and `effective_to`. Defaults when omitted: count 7 days, trends 30 days (day) or 2 days (hour), top 7 days, breakdown 30 days.

**Every response includes a `meta` block:**

```json
"meta": {
  "effective_from": "2026-10-24T00:00:00+00:00",
  "effective_to": "2026-11-01T00:00:00+00:00",
  "updated": "updated 3 seconds ago",
  "data_age_seconds": 3.1,
  "query_ms": 4.2
}
```

**Status codes:** `200` OK, `400` bad date range, `401` missing or invalid key, `403` key does not belong to that tenant, `429` too many concurrent requests for that tenant.

**PulseGuard admin endpoints** (header `X-Admin-Key`): `GET /admin/status`, `GET /admin/verify/{tenant}`, `POST /admin/verify-and-repair/{tenant}`, `POST /admin/rebuild/{tenant}`.

## 10. Business rules

Defined once in `pulse/rules.py` and used by the worker, the API, the checker and the benchmarks, so they cannot disagree.

| Status | Counts in total orders | Counts in valid orders, sales and top-N |
|---|---|---|
| placed | yes | yes |
| shipped | yes | yes |
| cancelled | yes | **no** |

- `total_orders`: every order created in the range, any status.
- `valid_orders`: total minus cancelled.
- `total_sales`: sum of `amount` over valid orders.

When an order moves from placed to cancelled: placed count -1, cancelled count +1, valid orders -1, sales reduced by the order amount, total orders unchanged. If the sales definition changes, edit `VALID_STATUSES` in `pulse/rules.py` and nothing else.

## 11. Design decisions

| Decision | Why |
|---|---|
| **Recompute affected buckets, never add or subtract deltas** | A status change (placed to cancelled) is easy to get wrong with delta math, and a retried event would be counted twice. Rewriting a bucket from the source is always correct and safe to repeat. |
| **Poll an indexed `updated_at`, no triggers** | Triggers would slow every normal write on the main database. Polling through the `(updated_at, order_id)` index costs almost nothing and never scans the table. |
| **Watermark saved in the same transaction as the work** | A crash can never advance progress past unprocessed changes. |
| **Overlap window (default 10 s)** | Rows from transactions that commit slightly late are not missed. Safe because recomputation is idempotent. |
| **Keyset pagination in batches** | Large backlogs are processed in bounded transactions. |
| **One advisory lock shared by worker and rebuilds** | A rebuild cannot be overwritten by a concurrent worker batch. |
| **Summary tables kept small** | `agg_orders_hourly` is keyed by tenant, hour and status. `agg_product_daily` is keyed by tenant, day and product. A dashboard query reads a few thousand rows however many orders exist. |
| **Product summaries recomputed per (tenant, day)** | One scan of a store's orders for one day refreshes all its products, so no extra index on the orders table is required. |
| **No response cache** | Removes the cache-leak class of bug (INC-02). If a cache is added later, `tenant_id` must be part of every key. |
| **UTC everywhere** | Hour and day buckets are unambiguous and identical in Python and SQL. |

## 12. Tenant security

- **The tenant in the URL is never trusted by itself.** The `X-API-Key` header is hashed and mapped to its owning tenant. If that differs from the tenant in the URL, the request gets `403`.
- API keys are stored only as **SHA-256 hashes**.
- All summary rows carry `tenant_id` and every query filters on it.
- **Per-tenant concurrency limit** (default 4 simultaneous requests, then `429`), so one tenant cannot hog the service.
- There is no shared response cache that could leak data between tenants.
- Test identities are API keys. A full login system is out of scope for the problem statement.

## 13. PulseGuard: verify, repair, monitor

PulseGuard is the built-in health module.

- **Verify:** runs the five standard dashboard queries from the summary tables and directly from `orders`, then compares them exactly.
- **Repair:** if anything differs, it rebuilds that tenant's summaries from the source and verifies again.
- **Monitor:** data age, worker backlog, last cycle time, per-endpoint latency (p50, p95, p99) and security counters (blocked cross-tenant attempts).

```bash
python -m scripts.check_correctness --sample 5          # biggest, median, smallest tenants
python -m scripts.check_correctness --all               # every tenant
python -m scripts.corrupt_summary --tenant 5            # demo: damage one summary on purpose
python -m scripts.check_correctness --tenant 5          # shows MISMATCH
python -m scripts.check_correctness --tenant 5 --repair # shows REPAIRED
python -m scripts.rebuild --all                         # rebuild everything from scratch
```

Run the checker when the worker has caught up. Changes that are still in flight can show as a temporary mismatch.

## 14. Demo walkthrough

1. Open the dashboard, pick a store and note its numbers and the "updated N seconds ago" label.
2. Simulate application traffic, here 2 new orders and 5 cancellations:
   ```bash
   python -m scripts.simulate_changes --tenant 5 --new 2 --cancel 5
   ```
3. Refresh a few seconds later. Total orders rose by 2, cancelled by 5, valid orders fell by 3, and sales dropped by the cancelled amounts. Top-10 rankings can move.
4. Run `python -m scripts.check_correctness --tenant 5`: the summary still matches the raw database.
5. Open the **PulseGuard** tab: data age, backlog, latency table.
6. Use the **tenant isolation test**: Store 1's key asking for Store 2's data is rejected, and the blocked-attempt counter increases.
7. Corrupt a summary with `corrupt_summary`, show `MISMATCH`, then run the checker with `--repair`.

Continuous traffic: `python -m scripts.simulate_changes --loop --rate 20`.

## 15. Performance results

### Observed so far

Measured on a laptop with **1 million orders and 10 tenants**, using the response-time table from the PulseGuard tab. These are times measured **inside the API** (small samples, includes a cold first request), not full browser round trips.

| Dashboard query | Requests | p50 | p95 |
|---|---|---|---|
| Counts and totals | 15 | 9.5 ms | 162 ms (cold start) |
| Trends | 14 | 9.1 ms | 19.3 ms |
| Top-10 products | 14 | 8.6 ms | 15.4 ms |
| Breakdown by status | 14 | 7.0 ms | 17.7 ms |

Other observations: data age 2 to 6 seconds, worker backlog 0, a worker cycle of about 650 ms, an update of 2 new and 5 cancelled orders reflected within seconds, and one cross-tenant attempt blocked.

### Targets and measurement status

| What we measure | Good | Great | Measured? |
|---|---|---|---|
| Query speed at 10M records | < 1 s | < 200 ms | 1M observed above, 10M pending |
| Freshness (add an order, see it) | < 5 min | < 30 s | Pending (benchmark not yet run) |
| Database safety (normal queries with vs without dashboard load) | < 25% slower | < 10% slower | Pending |
| Correctness vs direct query | most queries | all queries | Spot-checked, full run pending |
| Growth 1M to 10M | < 5x slower | < 2x slower | Pending |

### How to produce the measurements

Start the worker and API first. Use `127.0.0.1` rather than `localhost` on Windows, because `localhost` can add a delay per request. Put `--api-url` before the sub-command.

```bash
python -m scripts.check_correctness --all
python -m scripts.benchmark --api-url http://127.0.0.1:8000 latency --reps 20
python -m scripts.benchmark --api-url http://127.0.0.1:8000 freshness --runs 5
python -m scripts.benchmark --api-url http://127.0.0.1:8000 impact --seconds 15 --load-procs 2 --include-direct
# after loading 1M rows and again after 10M rows:
python -m scripts.benchmark compare benchmarks/latency_1000000.json benchmarks/latency_10000000.json
```

Results are saved to `benchmarks/*.json`. The `impact` benchmark compares normal application queries with no dashboard load, with load through this API, and (with `--include-direct`) with the naive approach of dashboards querying `orders` directly.

> Benchmarks on a single laptop run the database, API and load generator together, so CPU competition can inflate the measured slowdown.

## 16. Configuration

Environment variables. Defaults work for local use.

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql://pulse:pulse@localhost:5432/pulse` | PostgreSQL connection string |
| `ADMIN_KEY` | `change-me-admin-key` | Password for the PulseGuard admin endpoints. You choose it, and the API and dashboard must use the same value |
| `API_URL` | `http://localhost:8000` | Where the dashboard finds the API (dashboard only) |
| `KEYS_FILE` | `data/api_keys.json` | Tenant API keys written by the data generator |
| `POLL_INTERVAL` | `5` | Seconds between worker cycles (lower means fresher data) |
| `BATCH_SIZE` | `5000` | Changed orders processed per transaction |
| `OVERLAP_SECONDS` | `10` | Re-read window for late commits |
| `MAX_CONCURRENT_PER_TENANT` | `4` | Simultaneous requests allowed per tenant |
| `POOL_MAX` | `20` | Maximum database connections used by the API |

Python does not read `.env` files by itself. Set these as environment variables (`$env:NAME = "value"` in PowerShell, `export NAME=value` elsewhere), or use Docker Compose.

## 17. Tests

```bash
pytest tests/test_rules.py          # unit tests, no database needed
```

Integration tests need a **disposable** database, because they drop its `public` schema:

```bash
createdb pulse_test
TEST_DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/pulse_test pytest -v
```

They cover: summaries equal direct queries, new and updated orders flow through the worker, reprocessing is idempotent, corruption is detected and repaired, `401` and `403` on every endpoint, API numbers equal database numbers, and the per-tenant limit.

## 18. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `password authentication failed for user ...` | The password in `DATABASE_URL` is wrong. Use your real PostgreSQL password. Special characters need URL-encoding (`@` is `%40`, `#` is `%23`, `/` is `%2F`). |
| Dashboard says `No API keys found` | `generate_data` has not run successfully yet, so `data/api_keys.json` does not exist. |
| `No module named 'psycopg'` | Packages were installed for a different Python. Run `python -m pip install -r requirements.txt` and start programs with `python -m uvicorn ...`. |
| Dashboard shows 3 to 4 second response times but the API reports a few ms | On Windows, `localhost` can resolve to IPv6 first. Set `$env:API_URL = "http://127.0.0.1:8000"` and restart the dashboard. |
| `Admin endpoint returned 401` | `ADMIN_KEY` differs between the API terminal and the dashboard terminal. |
| `GET / 404` in the API log | Normal. The API has no home page. Open `/docs`. |
| Data does not update after changes | The worker is not running, or the laptop went to sleep. |
| `docker compose` cannot connect to the Docker API | Docker Desktop is not running. |

## 19. Adapting to a different schema

The aggregation layer expects `orders(order_id, tenant_id, product_id, quantity, amount, status, created_at, updated_at)`, `products(product_id, tenant_id, name)` and a `tenants` table holding API-key hashes. If another schema is used:

- the easiest fix is a **view named `orders`** that renames and maps the columns, or edit the SQL in `pulse/aggregator.py`, `pulse/queries.py` and `pulse/direct.py`;
- if there is no `updated_at`, agree how updates are signalled before choosing another change-detection method;
- keep the `(updated_at, order_id)` and `(tenant_id, created_at)` indexes from `sql/indexes.sql`;
- keep the status rules in `pulse/rules.py` in sync with the business definition.


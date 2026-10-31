"""
Integration tests need a DISPOSABLE PostgreSQL database (they drop the public schema!):

    createdb pulse_test        # or: docker compose exec db createdb -U pulse pulse_test
    TEST_DATABASE_URL=postgresql://pulse:pulse@localhost:5432/pulse_test pytest -v

Without TEST_DATABASE_URL, only the pure unit tests run.
"""
import os

import pytest

TEST_URL = os.getenv("TEST_DATABASE_URL")
if TEST_URL:
    os.environ["DATABASE_URL"] = TEST_URL  # must happen before pulse.* is imported

KEYS = {1: "test-key-store-1", 2: "test-key-store-2", 3: "test-key-store-3"}


@pytest.fixture(scope="session")
def db():
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    from pulse.aggregator import rebuild_all
    from pulse.auth import hash_key
    from pulse.db import apply_sql_file, connect

    conn = connect()
    conn.execute("DROP SCHEMA public CASCADE")
    conn.execute("CREATE SCHEMA public")
    apply_sql_file(conn, "schema.sql")
    apply_sql_file(conn, "indexes.sql")
    for tid, key in KEYS.items():
        conn.execute("INSERT INTO tenants VALUES (%s, %s, %s)", (tid, f"Store {tid}", hash_key(key)))
        for p in range(1, 6):
            conn.execute("INSERT INTO products VALUES (%s, %s, %s)", (tid * 100 + p, tid, f"P{tid}-{p}"))
    conn.execute("SELECT setseed(0.42)")
    conn.execute(
        """
        INSERT INTO orders (tenant_id, customer_id, product_id, quantity, amount, status, created_at, updated_at)
        SELECT t, 1, t * 100 + 1 + (g % 5), 1 + (g % 4), round((10 + random() * 900)::numeric, 2),
               (ARRAY['placed','shipped','cancelled'])[1 + (g % 3)],
               now() - (random() * 20) * interval '1 day', now() - interval '1 hour'
        FROM generate_series(1, 3) t, generate_series(1, 400) g
        """
    )
    rebuild_all(conn)
    yield conn
    conn.close()

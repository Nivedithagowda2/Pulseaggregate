"""Needs TEST_DATABASE_URL (see conftest.py)."""
import pytest

from tests.conftest import KEYS


def _agg_snapshot(db):
    hourly = db.execute("SELECT * FROM agg_orders_hourly ORDER BY tenant_id, hour, status").fetchall()
    daily = db.execute("SELECT * FROM agg_product_daily ORDER BY tenant_id, day, product_id").fetchall()
    return hourly, daily


def test_summaries_match_direct_queries(db):
    from pulse.guard import verify_tenant
    for tid in KEYS:
        res = verify_tenant(db, tid)
        assert res["ok"], res["checks"]


def test_new_and_updated_orders_flow_through_worker(db):
    from pulse.guard import verify_tenant
    from pulse.worker import run_cycle

    db.execute(
        "INSERT INTO orders (tenant_id, customer_id, product_id, quantity, amount, status, created_at, updated_at) "
        "VALUES (1, 1, 101, 2, 2000.00, 'placed', now(), now())"
    )
    # placed -> cancelled must move the order between status counts and remove its sales
    db.execute(
        "UPDATE orders SET status = 'cancelled', updated_at = now() "
        "WHERE order_id = (SELECT order_id FROM orders WHERE tenant_id = 2 AND status = 'placed' LIMIT 1)"
    )
    assert run_cycle() >= 2
    for tid in KEYS:
        assert verify_tenant(db, tid)["ok"]


def test_reprocessing_is_idempotent(db):
    from pulse.worker import run_cycle
    run_cycle()
    before = _agg_snapshot(db)
    run_cycle()
    run_cycle()  # the overlap window re-reads recent rows; results must not change
    assert _agg_snapshot(db) == before


def test_corruption_is_detected_and_repaired(db):
    from pulse.guard import verify_and_repair, verify_tenant
    db.execute(
        "UPDATE agg_orders_hourly SET order_count = order_count + 5 "
        "WHERE (tenant_id, hour, status) IN (SELECT tenant_id, hour, status FROM agg_orders_hourly "
        "WHERE tenant_id = 3 AND status = 'shipped' ORDER BY hour DESC LIMIT 1)"
    )
    assert not verify_tenant(db, 3)["ok"]
    result = verify_and_repair(db, 3)
    assert result["repaired"] and result["ok"]


@pytest.fixture()
def client(db):
    from fastapi.testclient import TestClient
    from pulse import auth
    from pulse.api import app
    auth.clear_cache()
    return TestClient(app)


def test_api_requires_key(client):
    assert client.get("/tenants/1/orders/count").status_code == 401
    assert client.get("/tenants/1/orders/count", headers={"X-API-Key": "wrong"}).status_code == 401


def test_cross_tenant_access_is_blocked_on_every_endpoint(client):
    headers = {"X-API-Key": KEYS[1]}
    for path in ("orders/count", "orders/trends", "products/top", "orders/breakdown"):
        assert client.get(f"/tenants/1/{path}", headers=headers).status_code == 200
        assert client.get(f"/tenants/2/{path}", headers=headers).status_code == 403
        assert client.get(f"/tenants/3/{path}", headers=headers).status_code == 403


def test_api_numbers_match_database(client, db):
    from pulse import direct
    from pulse.catalog import effective_range
    start, end = effective_range("count")
    expected = direct.totals(db, 1, start, end)
    body = client.get("/tenants/1/orders/count", headers={"X-API-Key": KEYS[1]}).json()
    assert body["total_orders"] == expected["total_orders"]
    assert body["valid_orders"] == expected["valid_orders"]
    assert body["total_sales"] == pytest.approx(float(expected["total_sales"]))
    assert "updated" in body["meta"]


def test_bad_range_returns_400(client):
    r = client.get("/tenants/1/orders/count?from=2026-10-31&to=2026-10-01", headers={"X-API-Key": KEYS[1]})
    assert r.status_code == 400


def test_per_tenant_concurrency_limit(client):
    from pulse.api import limiter
    taken = 0
    while limiter.try_acquire(1):
        taken += 1
    try:
        assert client.get("/tenants/1/orders/count", headers={"X-API-Key": KEYS[1]}).status_code == 429
        # another tenant is unaffected
        assert client.get("/tenants/2/orders/count", headers={"X-API-Key": KEYS[2]}).status_code == 200
    finally:
        for _ in range(taken):
            limiter.release(1)


def test_admin_requires_key(client):
    assert client.get("/admin/status").status_code == 401

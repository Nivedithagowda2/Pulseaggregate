"""
PulseGuard: correctness verification and self-repair.

Runs the standard dashboard queries from the summary tables AND directly from the
orders table, compares them exactly, and can rebuild a tenant's summaries if they differ.
"""
import time
from datetime import datetime
from decimal import Decimal

from .aggregator import rebuild_tenant
from .catalog import QUERY_NAMES, run_direct, run_summary
from .rules import utcnow


def _norm(value):
    """Make aggregated and direct results comparable (Decimal 12 == int 12, 5.50 == 5.5)."""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    return value


def verify_tenant(conn, tenant_id, now=None):
    """Compare summary results with direct database results for one tenant."""
    now = now or utcnow()
    t0 = time.perf_counter()
    checks = []
    for name in QUERY_NAMES:
        aggregated = _norm(run_summary(conn, name, tenant_id, now))
        direct = _norm(run_direct(conn, name, tenant_id, now))
        ok = aggregated == direct
        check = {"query": name, "ok": ok}
        if not ok:
            check["aggregated"] = aggregated
            check["direct"] = direct
        checks.append(check)
    return {
        "tenant_id": tenant_id,
        "ok": all(c["ok"] for c in checks),
        "failed": [c["query"] for c in checks if not c["ok"]],
        "checks": checks,
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def verify_and_repair(conn, tenant_id):
    """Verify; if anything differs, rebuild that tenant's summaries and verify again."""
    before = verify_tenant(conn, tenant_id)
    if before["ok"]:
        return {"tenant_id": tenant_id, "ok": True, "repaired": False, "before": before}
    rebuild_tenant(conn, tenant_id)
    after = verify_tenant(conn, tenant_id)
    return {"tenant_id": tenant_id, "ok": after["ok"], "repaired": True, "before": before, "after": after}

"""
Streamlit UI: a simple tenant dashboard + the PulseGuard monitoring page.

  streamlit run dashboard/app.py
Environment: API_URL (default http://localhost:8000), KEYS_FILE, ADMIN_KEY.
"""
import json
import os
import time
from pathlib import Path

import httpx
import pandas as pd
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000")
KEYS_FILE = os.getenv("KEYS_FILE", "data/api_keys.json")
ADMIN_KEY = os.getenv("ADMIN_KEY", "change-me-admin-key")

st.set_page_config(page_title="PulseAggregate", layout="wide")


@st.cache_data
def load_keys():
    path = Path(KEYS_FILE)
    if not path.exists():
        return {}
    return {int(k): v["api_key"] for k, v in json.loads(path.read_text())["tenants"].items()}


def call(method, path, key=None, admin=False, params=None):
    headers = {"X-Admin-Key": ADMIN_KEY} if admin else {"X-API-Key": key or ""}
    t0 = time.perf_counter()
    r = httpx.request(method, f"{API_URL}{path}", headers=headers, params=params, timeout=120)
    return r, (time.perf_counter() - t0) * 1000


keys = load_keys()
if not keys:
    st.error(f"No API keys found at {KEYS_FILE}. Run `python -m scripts.generate_data` first.")
    st.stop()

st.title("PulseAggregate")
st.caption("Fast, fresh, tenant-isolated dashboard analytics. Monitoring by PulseGuard.")
tab_dash, tab_guard = st.tabs(["Store dashboard", "PulseGuard monitoring"])

# ------------------------------------------------------------------ dashboard
with tab_dash:
    tenant_id = st.selectbox("Store (tenant)", sorted(keys), format_func=lambda t: f"Store {t}")
    gran = st.radio("Trend granularity", ["day", "hour"], horizontal=True)
    auto = st.checkbox("Auto-refresh every 5 s")
    key = keys[tenant_id]

    r_count, ms_count = call("GET", f"/tenants/{tenant_id}/orders/count", key)
    r_trend, ms_trend = call("GET", f"/tenants/{tenant_id}/orders/trends", key, params={"granularity": gran})
    r_top, ms_top = call("GET", f"/tenants/{tenant_id}/products/top", key)
    r_brk, ms_brk = call("GET", f"/tenants/{tenant_id}/orders/breakdown", key)

    if not all(r.status_code == 200 for r in (r_count, r_trend, r_top, r_brk)):
        st.error(f"API error: {[r.status_code for r in (r_count, r_trend, r_top, r_brk)]}")
        st.stop()

    c, t, top, brk = r_count.json(), r_trend.json(), r_top.json(), r_brk.json()
    st.info(f"Data {c['meta']['updated']}  |  range {c['meta']['effective_from'][:10]} to {c['meta']['effective_to'][:10]} (last 7 days)")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total orders", f"{c['total_orders']:,}")
    m2.metric("Valid orders (not cancelled)", f"{c['valid_orders']:,}")
    m3.metric("Cancelled", f"{c['cancelled_orders']:,}")
    m4.metric("Total sales", f"{c['total_sales']:,.2f}")

    left, right = st.columns(2)
    with left:
        st.subheader(f"Orders per {gran}")
        df = pd.DataFrame(t["points"])
        if not df.empty:
            st.line_chart(df.set_index("bucket")["orders"])
    with right:
        st.subheader("Orders by status")
        bdf = pd.DataFrame(brk["by_status"])
        st.bar_chart(bdf.set_index("status")["orders"])

    st.subheader("Top 10 products by sales (last 7 days)")
    st.dataframe(pd.DataFrame(top["products"]), hide_index=True)

    st.caption(f"API response times: count {ms_count:.0f} ms | trends {ms_trend:.0f} ms | "
               f"top-10 {ms_top:.0f} ms | breakdown {ms_brk:.0f} ms (includes this page's HTTP round trip)")
    if auto:
        time.sleep(5)
        st.rerun()

# ------------------------------------------------------------------ PulseGuard
with tab_guard:
    st.subheader("Freshness, backlog and latency")
    r, _ = call("GET", "/admin/status", admin=True)
    if r.status_code != 200:
        st.error(f"Admin endpoint returned {r.status_code}. Check ADMIN_KEY.")
    else:
        s = r.json()
        f = s["freshness"]
        a, b, c2, d = st.columns(4)
        a.metric("Data age", f"{f['data_age_seconds']} s" if f["data_age_seconds"] is not None else "n/a")
        b.metric("Backlog (changed orders waiting)", f["backlog_changed_orders"])
        c2.metric("Last worker cycle", f"{f['last_cycle_ms']} ms")
        d.metric("Orders processed (total)", f"{f['rows_processed_total']:,}")
        lat = pd.DataFrame(s["api_latency"]).T
        if not lat.empty:
            st.dataframe(lat)
        st.write("Security counters:", s["security_counters"] or "none yet")

    st.divider()
    st.subheader("Correctness check")
    gt = st.selectbox("Tenant to verify", sorted(keys), key="guard_tenant", format_func=lambda t: f"Store {t}")
    col1, col2 = st.columns(2)
    if col1.button("Verify against the main database"):
        r, _ = call("GET", f"/admin/verify/{gt}", admin=True)
        res = r.json()
        (st.success if res["ok"] else st.error)(
            f"{'All 5 dashboard queries match' if res['ok'] else 'Mismatch in: ' + ', '.join(res['failed'])} "
            f"({res['duration_ms']} ms)")
        st.json(res["checks"])
    if col2.button("Verify and auto-repair"):
        r, _ = call("POST", f"/admin/verify-and-repair/{gt}", admin=True)
        res = r.json()
        if res["repaired"]:
            st.warning(f"Found mismatches in {res['before']['failed']} - rebuilt from source. "
                       f"Re-check: {'OK' if res['ok'] else 'STILL MISMATCHED'}")
        else:
            st.success("Nothing to repair: summaries already match.")

    st.divider()
    st.subheader("Tenant isolation test")
    attacker = st.selectbox("Use the API key of", sorted(keys), key="attacker", format_func=lambda t: f"Store {t}")
    victim = st.selectbox("...to request the data of", sorted(keys), index=min(1, len(keys) - 1), key="victim",
                          format_func=lambda t: f"Store {t}")
    if st.button("Try it"):
        r, _ = call("GET", f"/tenants/{victim}/orders/count", keys[attacker])
        if attacker == victim:
            st.success(f"Own data: HTTP {r.status_code} (allowed)")
        elif r.status_code in (401, 403):
            st.success(f"Blocked: HTTP {r.status_code} - Store {attacker} cannot read Store {victim}")
        else:
            st.error(f"LEAK! HTTP {r.status_code}")

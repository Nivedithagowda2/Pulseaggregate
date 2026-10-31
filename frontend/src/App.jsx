import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ShieldCheck,
  RefreshCw,
  Database,
  ShoppingCart,
  IndianRupee,
  AlertTriangle,
  CheckCircle,
  Store,
} from "lucide-react";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  BarChart,
  Bar,
} from "recharts";
import "./App.css";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }

  return response.json();
}

function MetricCard({ icon: Icon, label, value, detail }) {
  return (
    <div className="metric-card">
      <div className="metric-top">
        <span>{label}</span>
        <div className="metric-icon">
          <Icon size={19} />
        </div>
      </div>
      <div className="metric-value">{value ?? "—"}</div>
      {detail && <div className="metric-detail">{detail}</div>}
    </div>
  );
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("en-IN");
}

function formatCurrency(value) {
  return `₹${Number(value || 0).toLocaleString("en-IN", {
    maximumFractionDigits: 2,
  })}`;
}

export default function App() {
  const [stores, setStores] = useState([]);
  const [tenant, setTenant] = useState("");
  const [granularity, setGranularity] = useState("day");

  const [count, setCount] = useState(null);
  const [trends, setTrends] = useState([]);
  const [products, setProducts] = useState([]);
  const [breakdown, setBreakdown] = useState([]);
  const [guard, setGuard] = useState(null);

  const [loading, setLoading] = useState(true);
  const [guardLoading, setGuardLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const loadStores = useCallback(async () => {
    try {
      const data = await request("/ui/stores");
      const availableStores = data.stores || [];
      setStores(availableStores);

      if (availableStores.length > 0) {
        setTenant((current) =>
          current && availableStores.includes(Number(current))
            ? current
            : String(availableStores[0])
        );
      } else {
        setError("No stores were found in data/api_keys.json.");
      }
    } catch (err) {
      setError(`Could not load stores: ${err.message}`);
    }
  }, []);

  const loadDashboard = useCallback(async (storeId, selectedGranularity) => {
    if (!storeId) return;

    setLoading(true);
    setError("");

    try {
      const base = `/ui/tenants/${storeId}`;

      const [countData, trendData, productData, breakdownData] =
        await Promise.all([
          request(`${base}/orders/count`),
          request(
            `${base}/orders/trends?granularity=${selectedGranularity}`
          ),
          request(`${base}/products/top?limit=10`),
          request(`${base}/orders/breakdown`),
        ]);

      setCount(countData);
      setTrends(trendData.points || []);
      setProducts(productData.products || []);
      setBreakdown(breakdownData.by_status || []);
    } catch (err) {
      setError(`Could not load dashboard: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadGuard = useCallback(async () => {
    setGuardLoading(true);

    try {
      const data = await request("/ui/admin/status");
      setGuard(data);
    } catch (err) {
      setError(`Could not load PulseGuard: ${err.message}`);
    } finally {
      setGuardLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStores();
    loadGuard();
  }, [loadStores, loadGuard]);

  useEffect(() => {
    if (tenant) {
      loadDashboard(tenant, granularity);
    }
  }, [tenant, granularity, loadDashboard]);

  async function runGuardAction(action) {
    if (!tenant) return;

    setActionLoading(true);
    setMessage("");
    setError("");

    try {
      let result;

      if (action === "verify") {
        result = await request(`/ui/admin/verify/${tenant}`);
      } else if (action === "repair") {
        result = await request(
          `/ui/admin/verify-and-repair/${tenant}`,
          { method: "POST" }
        );
      } else {
        result = await request(`/ui/admin/rebuild/${tenant}`, {
          method: "POST",
        });
      }

      setMessage(
        result.message ||
          result.status ||
          "Operation completed successfully."
      );

      await Promise.all([
        loadDashboard(tenant, granularity),
        loadGuard(),
      ]);
    } catch (err) {
      setError(`Operation failed: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  }

  const freshness = guard?.freshness || {};
  const latency = guard?.api_latency || {};

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Activity size={23} />
          </div>
          <div>
            <h2>PulseAggregate</h2>
            <p>Analytics platform</p>
          </div>
        </div>

        <div className="side-label">WORKSPACE</div>

        <div className="side-link active">
          <Database size={18} />
          Dashboard
        </div>

        <div className="side-link">
          <ShieldCheck size={18} />
          PulseGuard
        </div>

        <div className="sidebar-bottom">
          <div className="system-status">
            <span className="status-dot" />
            Local system
            <strong>Connected</strong>
          </div>
          <p>PulseAggregate Demo</p>
        </div>
      </aside>

      <main className="main">
        <header className="header">
          <div>
            <p className="eyebrow">OVERVIEW</p>
            <h1>Analytics Dashboard</h1>
            <p className="subtitle">
              Monitor tenant orders, sales and aggregation health.
            </p>
          </div>

          <div className="header-controls">
            <label htmlFor="store-select">Select store</label>
            <div className="select-wrap">
              <Store size={17} />
              <select
                id="store-select"
                value={tenant}
                onChange={(event) => setTenant(event.target.value)}
              >
                {stores.map((storeId) => (
                  <option key={storeId} value={storeId}>
                    Store {storeId}
                  </option>
                ))}
              </select>
            </div>
            <button
              className="refresh-button"
              onClick={() => {
                loadDashboard(tenant, granularity);
                loadGuard();
              }}
              disabled={!tenant || loading}
            >
              <RefreshCw size={16} />
              Refresh
            </button>
          </div>
        </header>

        {error && (
          <div className="notice error-notice">
            <AlertTriangle size={18} />
            <span>{error}</span>
          </div>
        )}

        {message && (
          <div className="notice success-notice">
            <CheckCircle size={18} />
            <span>{message}</span>
            <button onClick={() => setMessage("")}>Dismiss</button>
          </div>
        )}

        <section className="section-heading">
          <div>
            <h2>Store {tenant || "—"} performance</h2>
            <p>Live data from your aggregation API</p>
          </div>
          {loading && <span className="loading-label">Loading data...</span>}
        </section>

        <section className="metrics-grid">
          <MetricCard
            icon={ShoppingCart}
            label="Total orders"
            value={formatNumber(count?.total_orders)}
            detail="All recorded orders"
          />
          <MetricCard
            icon={CheckCircle}
            label="Valid orders"
            value={formatNumber(count?.valid_orders)}
            detail="Orders included in sales"
          />
          <MetricCard
            icon={IndianRupee}
            label="Total sales"
            value={formatCurrency(count?.total_sales)}
            detail="Valid order sales"
          />
          <MetricCard
            icon={AlertTriangle}
            label="Cancelled orders"
            value={formatNumber(count?.cancelled_orders)}
            detail="Cancelled order count"
          />
        </section>

        <section className="charts-grid">
          <div className="panel trend-panel">
            <div className="panel-heading">
              <div>
                <h3>Order trends</h3>
                <p>Orders over time</p>
              </div>
              <select
                value={granularity}
                onChange={(event) => setGranularity(event.target.value)}
                aria-label="Trend interval"
              >
                <option value="day">Daily</option>
                <option value="hour">Hourly</option>
              </select>
            </div>

            <div className="chart-area">
              {trends.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={trends}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis
                      dataKey="bucket"
                      tick={{ fontSize: 11 }}
                      tickLine={false}
                    />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Line
                      type="monotone"
                      dataKey="orders"
                      stroke="#7772d8"
                      strokeWidth={3}
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="empty-state">No trend data available.</div>
              )}
            </div>
          </div>

          <div className="panel breakdown-panel">
            <div className="panel-heading">
              <div>
                <h3>Order breakdown</h3>
                <p>Orders grouped by status</p>
              </div>
            </div>

            <div className="chart-area">
              {breakdown.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={breakdown}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} />
                    <XAxis dataKey="status" tick={{ fontSize: 11 }} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <Tooltip />
                    <Bar
                      dataKey="orders"
                      fill="#75b9a5"
                      radius={[7, 7, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="empty-state">
                  No breakdown data available.
                </div>
              )}
            </div>
          </div>
        </section>

        <section className="panel products-panel">
          <div className="panel-heading">
            <div>
              <h3>Top products</h3>
              <p>Best-performing products for this store</p>
            </div>
            <span className="small-tag">Top 10</span>
          </div>

          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Orders</th>
                  <th>Sales</th>
                </tr>
              </thead>
              <tbody>
                {products.length > 0 ? (
                  products.map((product, index) => (
                    <tr key={product.product_id || product.product_name || index}>
                      <td>
                        <span className="product-number">{index + 1}</span>
                        {product.product_name ||
                          product.product ||
                          product.name ||
                          "Product"}
                      </td>
                      <td>
                        {formatNumber(
                          product.orders ?? product.order_count
                        )}
                      </td>
                      <td>
                        {formatCurrency(
                          product.sales ?? product.total_sales
                        )}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan="3" className="empty-table">
                      No product data available.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="section-heading guard-title">
          <div>
            <p className="eyebrow">SYSTEM MONITORING</p>
            <h2>PulseGuard</h2>
            <p>Aggregation health and data integrity monitoring</p>
          </div>
          <button
            className="refresh-button"
            onClick={loadGuard}
            disabled={guardLoading}
          >
            <RefreshCw size={16} />
            {guardLoading ? "Checking..." : "Check health"}
          </button>
        </section>

        <section className="guard-grid">
          <div className="guard-card">
            <div className="guard-card-title">
              <Activity size={18} />
              Data freshness
            </div>
            <strong>
              {freshness.data_age_seconds != null
                ? `${Number(freshness.data_age_seconds).toFixed(1)}s`
                : "—"}
            </strong>
            <p>Age of latest aggregation data</p>
          </div>

          <div className="guard-card">
            <div className="guard-card-title">
              <Database size={18} />
              Processing backlog
            </div>
            <strong>
              {formatNumber(freshness.backlog_changed_orders)}
            </strong>
            <p>Changed orders waiting to process</p>
          </div>

          <div className="guard-card">
            <div className="guard-card-title">
              <RefreshCw size={18} />
              Last cycle
            </div>
            <strong>
              {freshness.last_cycle_ms != null
                ? `${Number(freshness.last_cycle_ms).toFixed(0)} ms`
                : "—"}
            </strong>
            <p>Most recent worker cycle duration</p>
          </div>

          <div className="guard-card">
            <div className="guard-card-title">
              <CheckCircle size={18} />
              Rows processed
            </div>
            <strong>
              {formatNumber(freshness.rows_processed_total)}
            </strong>
            <p>Total rows processed by the worker</p>
          </div>
        </section>

        <section className="panel actions-panel">
          <div className="panel-heading">
            <div>
              <h3>Data integrity controls</h3>
              <p>Verify summaries and repair or rebuild them</p>
            </div>
            <ShieldCheck size={22} />
          </div>

          <div className="action-buttons">
            <button
              className="action-button secondary"
              disabled={!tenant || actionLoading}
              onClick={() => runGuardAction("verify")}
            >
              Verify summaries
            </button>
            <button
              className="action-button secondary"
              disabled={!tenant || actionLoading}
              onClick={() => runGuardAction("repair")}
            >
              Verify & repair
            </button>
            <button
              className="action-button primary"
              disabled={!tenant || actionLoading}
              onClick={() => runGuardAction("rebuild")}
            >
              Rebuild summaries
            </button>
          </div>
          {actionLoading && (
            <p className="action-status">Running operation...</p>
          )}
        </section>

        <footer className="footer">
          PulseAggregate · Tenant analytics and PulseGuard monitoring
        </footer>
      </main>
    </div>
  );
}
-- PulseAggregate schema.
-- Source tables (orders, products, customers) stand in for the organisers' data.
-- Summary tables (agg_*) are what the dashboards read.

CREATE SEQUENCE IF NOT EXISTS orders_order_id_seq;

CREATE TABLE IF NOT EXISTS tenants (
    tenant_id    INT  PRIMARY KEY,
    name         TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE          -- sha256 of the tenant's API key
);

CREATE TABLE IF NOT EXISTS products (
    product_id INT  PRIMARY KEY,
    tenant_id  INT  NOT NULL,
    name       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS products_tenant_idx ON products (tenant_id);

CREATE TABLE IF NOT EXISTS customers (
    customer_id INT  PRIMARY KEY,
    tenant_id   INT  NOT NULL,
    name        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id    BIGINT PRIMARY KEY DEFAULT nextval('orders_order_id_seq'),
    tenant_id   INT           NOT NULL,
    customer_id INT           NOT NULL,
    product_id  INT           NOT NULL,
    quantity    INT           NOT NULL DEFAULT 1,
    amount      NUMERIC(12,2) NOT NULL,
    status      TEXT          NOT NULL CHECK (status IN ('placed', 'shipped', 'cancelled')),
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- Hourly rollup per tenant and status. Serves counts, trends and status breakdown.
CREATE TABLE IF NOT EXISTS agg_orders_hourly (
    tenant_id   INT           NOT NULL,
    hour        TIMESTAMPTZ   NOT NULL,
    status      TEXT          NOT NULL,
    order_count BIGINT        NOT NULL,
    sales       NUMERIC(18,2) NOT NULL,   -- sum of amount for this status
    PRIMARY KEY (tenant_id, hour, status)
);

-- Daily rollup per tenant and product (valid, i.e. non-cancelled, orders only).
CREATE TABLE IF NOT EXISTS agg_product_daily (
    tenant_id   INT           NOT NULL,
    day         DATE          NOT NULL,
    product_id  INT           NOT NULL,
    order_count BIGINT        NOT NULL,
    units       BIGINT        NOT NULL,
    sales       NUMERIC(18,2) NOT NULL,
    PRIMARY KEY (tenant_id, day, product_id)
);

-- Single-row table: worker progress and freshness.
CREATE TABLE IF NOT EXISTS agg_state (
    id              INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    watermark       TIMESTAMPTZ,       -- newest orders.updated_at fully processed
    last_refresh_at TIMESTAMPTZ,       -- start of the last completed worker cycle
    last_cycle_ms   INT,
    rows_processed  BIGINT NOT NULL DEFAULT 0
);
INSERT INTO agg_state (id) VALUES (1) ON CONFLICT DO NOTHING;

-- Tiny table used by the benchmark to simulate "normal app writes".
CREATE TABLE IF NOT EXISTS app_probe (
    id         BIGSERIAL PRIMARY KEY,
    tenant_id  INT,
    payload    TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

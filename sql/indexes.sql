-- Indexes on the source table that the aggregation layer needs.
-- Created AFTER bulk loading (much faster). Safe to run repeatedly.

-- Worker change detection: keyset pagination over (updated_at, order_id).
CREATE INDEX IF NOT EXISTS orders_updated_idx ON orders (updated_at, order_id);

-- Bucket recomputation and direct (verification) queries: tenant + time range.
CREATE INDEX IF NOT EXISTS orders_tenant_created_idx ON orders (tenant_id, created_at);

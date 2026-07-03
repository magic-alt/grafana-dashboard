CREATE TABLE IF NOT EXISTS stock_prices (
    symbol TEXT NOT NULL,
    price_time TIMESTAMPTZ NOT NULL,
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    adj_close NUMERIC,
    volume BIGINT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, price_time)
);

CREATE INDEX IF NOT EXISTS idx_stock_prices_time ON stock_prices (price_time);
CREATE INDEX IF NOT EXISTS idx_stock_prices_symbol_time ON stock_prices (symbol, price_time DESC);

CREATE TABLE IF NOT EXISTS stock_indicators (
    symbol TEXT NOT NULL,
    price_time TIMESTAMPTZ NOT NULL,
    close NUMERIC,
    daily_return_pct NUMERIC,
    ma20 NUMERIC,
    ma60 NUMERIC,
    volatility20 NUMERIC,
    drawdown_pct NUMERIC,
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, price_time)
);

CREATE INDEX IF NOT EXISTS idx_stock_indicators_time ON stock_indicators (price_time);
CREATE INDEX IF NOT EXISTS idx_stock_indicators_symbol_time ON stock_indicators (symbol, price_time DESC);

CREATE TABLE IF NOT EXISTS observability_runs (
    run_id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    mode TEXT NOT NULL,
    symbols TEXT[] NOT NULL,
    price_rows INTEGER NOT NULL,
    indicator_rows INTEGER NOT NULL,
    download_ms NUMERIC,
    normalize_ms NUMERIC,
    analysis_ms NUMERIC,
    store_prices_ms NUMERIC,
    store_indicators_ms NUMERIC,
    grafana_query_ms NUMERIC,
    browser_render_ms NUMERIC,
    total_ms NUMERIC,
    trace_id TEXT,
    status TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_observability_runs_completed_at ON observability_runs (completed_at DESC);

CREATE OR REPLACE VIEW stock_daily_returns AS
SELECT
    symbol,
    price_time,
    close,
    (close / NULLIF(LAG(close) OVER (PARTITION BY symbol ORDER BY price_time), 0) - 1) * 100 AS daily_return_pct
FROM stock_prices
WHERE close IS NOT NULL;

CREATE OR REPLACE VIEW stock_summary AS
WITH ranked AS (
    SELECT
        symbol,
        price_time,
        close,
        volume,
        ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY price_time DESC) AS rn
    FROM stock_prices
    WHERE close IS NOT NULL
)
SELECT
    cur.symbol,
    cur.price_time,
    cur.close,
    prev.close AS previous_close,
    cur.close - prev.close AS day_change,
    (cur.close / NULLIF(prev.close, 0) - 1) * 100 AS day_change_pct,
    cur.volume
FROM ranked cur
LEFT JOIN ranked prev
    ON prev.symbol = cur.symbol
   AND prev.rn = 2
WHERE cur.rn = 1;

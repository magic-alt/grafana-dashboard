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
    pipeline_total_ms NUMERIC,
    critical_path_ms NUMERIC,
    total_ms NUMERIC,
    dominant_stage TEXT,
    reason_code TEXT,
    reason_summary TEXT,
    trace_id TEXT,
    status TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_observability_runs_completed_at ON observability_runs (completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_observability_runs_critical_path ON observability_runs (critical_path_ms);

CREATE TABLE IF NOT EXISTS lean_backtest_runs (
    run_id UUID PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    symbol TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    fast INTEGER NOT NULL,
    slow INTEGER NOT NULL,
    cash NUMERIC NOT NULL,
    data_source TEXT NOT NULL,
    data_rows INTEGER,
    data_first_date DATE,
    data_last_date DATE,
    overwrite_data BOOLEAN NOT NULL DEFAULT false,
    docker_image TEXT NOT NULL,
    lean_container_name TEXT,
    result_json_path TEXT,
    summary_json_path TEXT,
    report_html_path TEXT,
    statistics JSONB NOT NULL DEFAULT '{}'::jsonb,
    trace_id TEXT,
    profile_query TEXT,
    engine_profile_query TEXT,
    critical_path_ms NUMERIC,
    pipeline_total_ms NUMERIC,
    total_ms NUMERIC,
    dominant_stage TEXT,
    reason_code TEXT,
    reason_summary TEXT,
    error TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS lean_backtest_stage_timings (
    run_id UUID NOT NULL REFERENCES lean_backtest_runs(run_id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    duration_ms NUMERIC NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, stage)
);

CREATE INDEX IF NOT EXISTS idx_lean_backtest_runs_completed_at ON lean_backtest_runs (completed_at DESC);
CREATE INDEX IF NOT EXISTS idx_lean_backtest_runs_critical_path ON lean_backtest_runs (critical_path_ms);
CREATE INDEX IF NOT EXISTS idx_lean_backtest_runs_symbol ON lean_backtest_runs (symbol, completed_at DESC);

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

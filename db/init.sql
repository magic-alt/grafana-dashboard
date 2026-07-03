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

WITH symbols(symbol, base_price) AS (
    VALUES
        ('AAPL', 200.0::numeric),
        ('MSFT', 450.0::numeric),
        ('NVDA', 180.0::numeric),
        ('TSLA', 350.0::numeric),
        ('SPY', 650.0::numeric),
        ('QQQ', 590.0::numeric)
), sample_days(day_index) AS (
    SELECT generate_series(0, 39)
)
INSERT INTO stock_prices (
    symbol, price_time, open, high, low, close, adj_close, volume, fetched_at
)
SELECT
    symbol,
    date_trunc('day', now()) - day_index * interval '1 day',
    base_price + day_index * 0.10,
    base_price + day_index * 0.10 + 1.0,
    base_price + day_index * 0.10 - 1.0,
    base_price + day_index * 0.10 + 0.5,
    base_price + day_index * 0.10 + 0.5,
    1000000 + day_index * 1000,
    now()
FROM symbols
CROSS JOIN sample_days
ON CONFLICT (symbol, price_time) DO UPDATE SET
    close = EXCLUDED.close,
    adj_close = EXCLUDED.adj_close,
    volume = EXCLUDED.volume,
    fetched_at = now();

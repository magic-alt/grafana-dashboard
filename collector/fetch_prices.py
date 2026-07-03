import logging
import os
import time
from datetime import timezone
from math import isnan

import pandas as pd
import psycopg
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential

from analysis import analyze_records
from observability_support import (
    configure_observability,
    profile_tags,
    set_span_attributes,
    span as obs_span,
)


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)


DB = {
    "host": os.getenv("DB_HOST", "postgres"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "stockdash"),
    "user": os.getenv("DB_USER", "stock"),
    "password": os.getenv("DB_PASSWORD", "stock"),
}

TICKERS = [item.strip().upper() for item in os.getenv("TICKERS", "AAPL,MSFT,NVDA,TSLA,SPY,QQQ").split(",") if item.strip()]
YAHOO_PERIOD = os.getenv("YAHOO_PERIOD", "1y")
YAHOO_INTERVAL = os.getenv("YAHOO_INTERVAL", "1d")
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "3600"))
ANALYSIS_LOAD_FACTOR = int(os.getenv("OBS_ANALYSIS_LOAD_FACTOR", "1"))


SCHEMA_SQL = [
    """
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
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_stock_prices_time ON stock_prices (price_time)",
    "CREATE INDEX IF NOT EXISTS idx_stock_prices_symbol_time ON stock_prices (symbol, price_time DESC)",
    """
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
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_stock_indicators_time ON stock_indicators (price_time)",
    "CREATE INDEX IF NOT EXISTS idx_stock_indicators_symbol_time ON stock_indicators (symbol, price_time DESC)",
    """
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
    )
    """,
    "ALTER TABLE observability_runs ADD COLUMN IF NOT EXISTS pipeline_total_ms NUMERIC",
    "ALTER TABLE observability_runs ADD COLUMN IF NOT EXISTS critical_path_ms NUMERIC",
    "ALTER TABLE observability_runs ADD COLUMN IF NOT EXISTS dominant_stage TEXT",
    "ALTER TABLE observability_runs ADD COLUMN IF NOT EXISTS reason_code TEXT",
    "ALTER TABLE observability_runs ADD COLUMN IF NOT EXISTS reason_summary TEXT",
    "CREATE INDEX IF NOT EXISTS idx_observability_runs_completed_at ON observability_runs (completed_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_observability_runs_critical_path ON observability_runs (critical_path_ms)",
    """
    WITH stage_values AS (
        SELECT
            run_id,
            COALESCE(download_ms, 0) AS download_ms,
            COALESCE(normalize_ms, 0) AS normalize_ms,
            COALESCE(analysis_ms, 0) AS analysis_ms,
            COALESCE(store_prices_ms, 0) AS store_prices_ms,
            COALESCE(store_indicators_ms, 0) AS store_indicators_ms,
            COALESCE(grafana_query_ms, 0) AS grafana_query_ms,
            COALESCE(browser_render_ms, 0) AS browser_render_ms
        FROM observability_runs
        WHERE status = 'ok'
    ), totals AS (
        SELECT
            run_id,
            download_ms + normalize_ms + analysis_ms + store_prices_ms + store_indicators_ms + grafana_query_ms AS pipeline_total_ms,
            download_ms + normalize_ms + analysis_ms + store_prices_ms + store_indicators_ms + grafana_query_ms + browser_render_ms AS stage_total_ms,
            GREATEST(download_ms, normalize_ms, analysis_ms, store_prices_ms, store_indicators_ms, grafana_query_ms, browser_render_ms) AS dominant_stage_ms,
            CASE GREATEST(download_ms, normalize_ms, analysis_ms, store_prices_ms, store_indicators_ms, grafana_query_ms, browser_render_ms)
                WHEN download_ms THEN 'download_prices'
                WHEN normalize_ms THEN 'normalize_prices'
                WHEN analysis_ms THEN 'analysis'
                WHEN store_prices_ms THEN 'store_prices'
                WHEN store_indicators_ms THEN 'store_indicators'
                WHEN grafana_query_ms THEN 'grafana_query'
                ELSE 'browser_render'
            END AS dominant_stage
        FROM stage_values
    ), explained AS (
        SELECT
            run_id,
            pipeline_total_ms,
            stage_total_ms,
            dominant_stage,
            CASE
                WHEN stage_total_ms > 0 AND dominant_stage_ms / stage_total_ms < 0.4 THEN 'mixed_path_latency'
                WHEN dominant_stage = 'download_prices' THEN 'market_data_source_latency'
                WHEN dominant_stage = 'normalize_prices' THEN 'symbol_normalization_latency'
                WHEN dominant_stage = 'analysis' THEN 'analysis_cpu_latency'
                WHEN dominant_stage = 'store_prices' THEN 'price_storage_latency'
                WHEN dominant_stage = 'store_indicators' THEN 'indicator_storage_latency'
                WHEN dominant_stage = 'grafana_query' THEN 'grafana_datasource_latency'
                WHEN dominant_stage = 'browser_render' THEN 'grafana_browser_render_latency'
                ELSE 'unknown_latency'
            END AS reason_code,
            CASE
                WHEN stage_total_ms > 0 AND dominant_stage_ms / stage_total_ms < 0.4 THEN '延迟不是由单一阶段主导，由多个阶段共同造成。'
                WHEN dominant_stage = 'download_prices' THEN '真实 Yahoo Finance 行情下载阶段占比最高，主要受行情源响应和网络耗时影响。'
                WHEN dominant_stage = 'normalize_prices' THEN '按股票代码拆分和标准化行情数据阶段占比最高，通常和 symbol 数量及返回数据形状有关。'
                WHEN dominant_stage = 'analysis' THEN '技术指标分析阶段占比最高，主要来自 MA、波动率、回撤和可见 CPU 负载计算。'
                WHEN dominant_stage = 'store_prices' THEN '价格数据写入 Postgres 阶段占比最高，通常和 upsert 行数、索引维护和数据库 I/O 有关。'
                WHEN dominant_stage = 'store_indicators' THEN '指标数据写入 Postgres 阶段占比最高，通常和指标行数、索引维护和数据库 I/O 有关。'
                WHEN dominant_stage = 'grafana_query' THEN 'Grafana datasource 查询阶段占比最高，说明展示层 SQL 查询或 datasource 往返耗时是主因。'
                WHEN dominant_stage = 'browser_render' THEN 'Grafana 浏览器渲染阶段占比最高，说明前端加载、面板查询和页面绘制是主因。'
                ELSE '没有可用于解释的阶段耗时数据。'
            END AS reason_summary
        FROM totals
    )
    UPDATE observability_runs runs
    SET
        pipeline_total_ms = COALESCE(runs.pipeline_total_ms, explained.pipeline_total_ms),
        critical_path_ms = COALESCE(runs.critical_path_ms, NULLIF(explained.stage_total_ms, 0), runs.total_ms),
        total_ms = COALESCE(runs.critical_path_ms, NULLIF(explained.stage_total_ms, 0), runs.total_ms),
        dominant_stage = COALESCE(runs.dominant_stage, explained.dominant_stage),
        reason_code = COALESCE(runs.reason_code, explained.reason_code),
        reason_summary = COALESCE(runs.reason_summary, explained.reason_summary)
    FROM explained
    WHERE runs.run_id = explained.run_id
      AND runs.status = 'ok'
    """,
    """
    CREATE OR REPLACE VIEW stock_daily_returns AS
    SELECT
        symbol,
        price_time,
        close,
        (close / NULLIF(LAG(close) OVER (PARTITION BY symbol ORDER BY price_time), 0) - 1) * 100 AS daily_return_pct
    FROM stock_prices
    WHERE close IS NOT NULL
    """,
    """
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
    WHERE cur.rn = 1
    """,
]


UPSERT_PRICES_SQL = """
INSERT INTO stock_prices (
    symbol, price_time, open, high, low, close, adj_close, volume, fetched_at
) VALUES (
    %(symbol)s, %(price_time)s, %(open)s, %(high)s, %(low)s, %(close)s,
    %(adj_close)s, %(volume)s, now()
)
ON CONFLICT (symbol, price_time) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    adj_close = EXCLUDED.adj_close,
    volume = EXCLUDED.volume,
    fetched_at = now();
"""


UPSERT_INDICATORS_SQL = """
INSERT INTO stock_indicators (
    symbol, price_time, close, daily_return_pct, ma20, ma60, volatility20, drawdown_pct, calculated_at
) VALUES (
    %(symbol)s, %(price_time)s, %(close)s, %(daily_return_pct)s, %(ma20)s, %(ma60)s,
    %(volatility20)s, %(drawdown_pct)s, now()
)
ON CONFLICT (symbol, price_time) DO UPDATE SET
    close = EXCLUDED.close,
    daily_return_pct = EXCLUDED.daily_return_pct,
    ma20 = EXCLUDED.ma20,
    ma60 = EXCLUDED.ma60,
    volatility20 = EXCLUDED.volatility20,
    drawdown_pct = EXCLUDED.drawdown_pct,
    calculated_at = now();
"""


def clean_number(value):
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return None if isnan(number) else number


def clean_int(value):
    if value is None or pd.isna(value):
        return None
    return int(value)


def normalize_time(value):
    ts = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def get_symbol_frame(data, symbol):
    if data.empty:
        return pd.DataFrame()

    if isinstance(data.columns, pd.MultiIndex):
        first_level = set(str(item).upper() for item in data.columns.get_level_values(0))
        if symbol in first_level:
            return data[symbol]
        return data.xs(symbol, axis=1, level=1, drop_level=True)

    return data


def records_from_frame(symbol, frame):
    if frame.empty:
        return []

    frame = frame.rename(columns={column: str(column).strip().lower().replace(" ", "_") for column in frame.columns})
    records = []

    for price_time, row in frame.dropna(how="all").iterrows():
        close = clean_number(row.get("close"))
        if close is None:
            continue

        records.append(
            {
                "symbol": symbol,
                "price_time": normalize_time(price_time),
                "open": clean_number(row.get("open")),
                "high": clean_number(row.get("high")),
                "low": clean_number(row.get("low")),
                "close": close,
                "adj_close": clean_number(row.get("adj_close")),
                "volume": clean_int(row.get("volume")),
            }
        )

    return records


def elapsed_ms(started):
    return round((time.perf_counter() - started) * 1000, 3)


@retry(stop=stop_after_attempt(8), wait=wait_exponential(multiplier=1, min=1, max=30))
def wait_for_db():
    with psycopg.connect(**DB) as conn:
        conn.execute("SELECT 1")


def ensure_schema():
    with psycopg.connect(**DB) as conn:
        with conn.cursor() as cur:
            for statement in SCHEMA_SQL:
                cur.execute(statement)
        conn.commit()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
def download_prices():
    logging.info("downloading prices tickers=%s period=%s interval=%s", ",".join(TICKERS), YAHOO_PERIOD, YAHOO_INTERVAL)
    data = yf.download(
        tickers=TICKERS,
        period=YAHOO_PERIOD,
        interval=YAHOO_INTERVAL,
        group_by="ticker",
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if data.empty:
        raise RuntimeError("Yahoo Finance returned no rows")
    return data


def store_records(records):
    if not records:
        return 0

    with profile_tags({"stage": "store_prices"}):
        with obs_span("stock.store_prices", {"stock.rows": len(records)}):
            with psycopg.connect(**DB) as conn:
                with conn.cursor() as cur:
                    cur.executemany(UPSERT_PRICES_SQL, records)
                conn.commit()

    return len(records)


def store_indicators(indicators):
    if not indicators:
        return 0

    with profile_tags({"stage": "store_indicators"}):
        with obs_span("stock.store_indicators", {"stock.rows": len(indicators)}):
            with psycopg.connect(**DB) as conn:
                with conn.cursor() as cur:
                    cur.executemany(UPSERT_INDICATORS_SQL, indicators)
                conn.commit()

    return len(indicators)


def run_once(load_factor=None, include_analysis=True):
    load_factor = ANALYSIS_LOAD_FACTOR if load_factor is None else int(load_factor)
    total_started = time.perf_counter()
    result = {
        "symbols": TICKERS,
        "period": YAHOO_PERIOD,
        "interval": YAHOO_INTERVAL,
        "price_rows": 0,
        "indicator_rows": 0,
        "stage_timings_ms": {},
        "per_symbol": [],
    }

    with obs_span(
        "stock.pipeline.run",
        {
            "stock.symbols": ",".join(TICKERS),
            "stock.symbol_count": len(TICKERS),
            "stock.period": YAHOO_PERIOD,
            "stock.interval": YAHOO_INTERVAL,
            "analysis.load_factor": load_factor,
        },
    ) as active_span:
        ensure_schema()

        started = time.perf_counter()
        with profile_tags({"stage": "download_prices"}):
            with obs_span(
                "stock.download_prices",
                {
                    "stock.symbols": ",".join(TICKERS),
                    "stock.symbol_count": len(TICKERS),
                    "stock.period": YAHOO_PERIOD,
                    "stock.interval": YAHOO_INTERVAL,
                },
            ):
                data = download_prices()
        result["stage_timings_ms"]["download_prices"] = elapsed_ms(started)

        all_records = []
        normalize_total_ms = 0.0
        for symbol in TICKERS:
            started = time.perf_counter()
            with profile_tags({"stage": "normalize_prices", "symbol": symbol}):
                with obs_span("stock.normalize_prices", {"stock.symbol": symbol}):
                    try:
                        frame = get_symbol_frame(data, symbol)
                    except Exception as exc:
                        logging.warning("unable to extract %s from Yahoo result: %s", symbol, exc)
                        continue
                    records = records_from_frame(symbol, frame)
            symbol_ms = elapsed_ms(started)
            normalize_total_ms += symbol_ms
            logging.info("prepared %s rows for %s", len(records), symbol)
            result["per_symbol"].append({"symbol": symbol, "price_rows": len(records), "normalize_ms": symbol_ms})
            all_records.extend(records)
        result["stage_timings_ms"]["normalize_prices"] = round(normalize_total_ms, 3)

        started = time.perf_counter()
        count = store_records(all_records)
        result["stage_timings_ms"]["store_prices"] = elapsed_ms(started)
        result["price_rows"] = count
        logging.info("upserted %s price rows", count)

        if include_analysis:
            started = time.perf_counter()
            with profile_tags({"stage": "analysis"}):
                with obs_span(
                    "stock.analysis",
                    {"stock.rows": len(all_records), "analysis.load_factor": load_factor},
                ):
                    indicators, analysis_meta = analyze_records(all_records, load_factor=load_factor)
            result["stage_timings_ms"]["analysis"] = elapsed_ms(started)
            result["analysis"] = analysis_meta

            by_symbol = {item["symbol"]: item for item in result["per_symbol"]}
            for item in analysis_meta.get("symbols", []):
                by_symbol.setdefault(item["symbol"], {"symbol": item["symbol"]}).update(item)

            started = time.perf_counter()
            indicator_count = store_indicators(indicators)
            result["stage_timings_ms"]["store_indicators"] = elapsed_ms(started)
            result["indicator_rows"] = indicator_count
            logging.info("upserted %s indicator rows", indicator_count)

        result["total_ms"] = elapsed_ms(total_started)
        set_span_attributes(
            active_span,
            {
                "stock.price_rows": result["price_rows"],
                "stock.indicator_rows": result["indicator_rows"],
                "stock.total_ms": result["total_ms"],
            },
        )

    return result


def main():
    if not TICKERS:
        raise SystemExit("TICKERS is empty")

    configure_observability(os.getenv("OTEL_SERVICE_NAME", "stock-collector"))
    wait_for_db()
    ensure_schema()
    logging.info("collector started refresh_seconds=%s", REFRESH_SECONDS)

    while True:
        try:
            run_once()
        except Exception:
            logging.exception("price refresh failed")
        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()

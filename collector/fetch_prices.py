import logging
import os
import time
from datetime import timezone
from math import isnan

import pandas as pd
import psycopg
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential


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


UPSERT_SQL = """
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


@retry(stop=stop_after_attempt(8), wait=wait_exponential(multiplier=1, min=1, max=30))
def wait_for_db():
    with psycopg.connect(**DB) as conn:
        conn.execute("SELECT 1")


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

    with psycopg.connect(**DB) as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT_SQL, records)
        conn.commit()

    return len(records)


def run_once():
    data = download_prices()
    all_records = []

    for symbol in TICKERS:
        try:
            frame = get_symbol_frame(data, symbol)
        except Exception as exc:
            logging.warning("unable to extract %s from Yahoo result: %s", symbol, exc)
            continue
        records = records_from_frame(symbol, frame)
        logging.info("prepared %s rows for %s", len(records), symbol)
        all_records.extend(records)

    count = store_records(all_records)
    logging.info("upserted %s price rows", count)


def main():
    if not TICKERS:
        raise SystemExit("TICKERS is empty")

    wait_for_db()
    logging.info("collector started refresh_seconds=%s", REFRESH_SECONDS)

    while True:
        try:
            run_once()
        except Exception:
            logging.exception("price refresh failed")
        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()

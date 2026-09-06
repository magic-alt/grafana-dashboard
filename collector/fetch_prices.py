"""Compatibility entrypoint for the modular stock reference workload.

Business/domain logic now lives under ``stock/``. This module intentionally keeps
legacy names used by smoke tests and observability-case scripts while runtime DDL
has been removed in favor of Alembic migrations.
"""

from __future__ import annotations

import logging
import os
import time

from obs_platform.config import DatabaseSettings
from observability_support import configure_observability
from stock.adapters.postgres import PostgresStockRepository
from stock.adapters.yahoo import YahooMarketDataSource
from stock.application import StockPipeline
from stock.config import StockSettings
from stock.domain import clean_int, clean_number, get_symbol_frame, normalize_time, records_from_frame

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)

DATABASE_SETTINGS = DatabaseSettings.from_env()
SETTINGS = StockSettings.from_env()
REPOSITORY = PostgresStockRepository(DATABASE_SETTINGS)
SOURCE = YahooMarketDataSource()
PIPELINE = StockPipeline(SETTINGS, SOURCE, REPOSITORY)

# Backward-compatible module constants used by the existing observability lab.
DB = REPOSITORY.connect_kwargs
TICKERS = list(SETTINGS.tickers)
YAHOO_PERIOD = SETTINGS.period
YAHOO_INTERVAL = SETTINGS.interval
REFRESH_SECONDS = SETTINGS.refresh_seconds
ANALYSIS_LOAD_FACTOR = SETTINGS.analysis_load_factor


def wait_for_db() -> None:
    REPOSITORY.wait_until_ready()


def ensure_schema() -> None:
    """Compatibility name: verify Alembic revision; never create/alter schema."""
    REPOSITORY.assert_schema_current()


def download_prices():
    return SOURCE.download(SETTINGS.tickers, SETTINGS.period, SETTINGS.interval)


def store_records(records):
    return REPOSITORY.store_prices(records)


def store_indicators(indicators):
    return REPOSITORY.store_indicators(indicators)


def run_once(load_factor=None, include_analysis=True):
    return PIPELINE.run_once(load_factor=load_factor, include_analysis=include_analysis)


def main() -> None:
    SETTINGS.validate()
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


__all__ = [
    "ANALYSIS_LOAD_FACTOR",
    "DB",
    "REFRESH_SECONDS",
    "TICKERS",
    "YAHOO_INTERVAL",
    "YAHOO_PERIOD",
    "clean_int",
    "clean_number",
    "download_prices",
    "ensure_schema",
    "get_symbol_frame",
    "normalize_time",
    "records_from_frame",
    "run_once",
    "store_indicators",
    "store_records",
    "wait_for_db",
]


if __name__ == "__main__":
    main()

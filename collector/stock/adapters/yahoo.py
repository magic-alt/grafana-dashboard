from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf
from tenacity import retry, stop_after_attempt, wait_exponential


class YahooMarketDataSource:
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    def download(self, tickers: tuple[str, ...], period: str, interval: str) -> pd.DataFrame:
        logging.info("downloading prices tickers=%s period=%s interval=%s", ",".join(tickers), period, interval)
        data = yf.download(
            tickers=list(tickers),
            period=period,
            interval=interval,
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if data.empty:
            raise RuntimeError("Yahoo Finance returned no rows")
        return data

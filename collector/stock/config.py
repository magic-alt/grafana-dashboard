from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StockSettings:
    tickers: tuple[str, ...]
    period: str
    interval: str
    refresh_seconds: int
    analysis_load_factor: int

    @classmethod
    def from_env(cls) -> StockSettings:
        tickers = tuple(
            item.strip().upper()
            for item in os.getenv("TICKERS", "AAPL,MSFT,NVDA,TSLA,SPY,QQQ").split(",")
            if item.strip()
        )
        return cls(
            tickers=tickers,
            period=os.getenv("YAHOO_PERIOD", "1y"),
            interval=os.getenv("YAHOO_INTERVAL", "1d"),
            refresh_seconds=int(os.getenv("REFRESH_SECONDS", "3600")),
            analysis_load_factor=int(os.getenv("OBS_ANALYSIS_LOAD_FACTOR", "1")),
        )

    def validate(self) -> None:
        if not self.tickers:
            raise ValueError("TICKERS is empty")
        if self.refresh_seconds < 1:
            raise ValueError("REFRESH_SECONDS must be positive")
        if self.analysis_load_factor < 0:
            raise ValueError("OBS_ANALYSIS_LOAD_FACTOR must be non-negative")

from __future__ import annotations

from typing import Protocol

import pandas as pd


class MarketDataSource(Protocol):
    def download(self, tickers: tuple[str, ...], period: str, interval: str) -> pd.DataFrame: ...


class StockRepository(Protocol):
    def wait_until_ready(self) -> None: ...

    def assert_schema_current(self) -> None: ...

    def store_prices(self, records: list[dict[str, object]]) -> int: ...

    def store_indicators(self, records: list[dict[str, object]]) -> int: ...

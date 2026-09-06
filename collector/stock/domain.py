from __future__ import annotations

from datetime import UTC
from math import isnan

import pandas as pd


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
    timestamp = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC)


def get_symbol_frame(data: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        first_level = {str(item).upper() for item in data.columns.get_level_values(0)}
        if symbol in first_level:
            return data[symbol]
        return data.xs(symbol, axis=1, level=1, drop_level=True)
    return data


def records_from_frame(symbol: str, frame: pd.DataFrame) -> list[dict[str, object]]:
    if frame.empty:
        return []
    normalized = frame.rename(
        columns={column: str(column).strip().lower().replace(" ", "_") for column in frame.columns}
    )
    records: list[dict[str, object]] = []
    for price_time, row in normalized.dropna(how="all").iterrows():
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

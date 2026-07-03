import math
import time

import pandas as pd

from observability_support import profile_tags, set_span_attributes, span as obs_span


def _number(value):
    if value is None or pd.isna(value):
        return None
    return float(value)


def _visible_cpu_analysis(values, load_factor):
    if load_factor <= 1 or not values:
        return 0.0

    checksum = 0.0
    iterations = max(1, int(load_factor)) * 120
    for iteration in range(iterations):
        drift = (iteration % 17) + 1
        for index, value in enumerate(values):
            normalized = value / drift
            checksum += math.sin(normalized) * math.cos((index + 1) / drift)
            checksum += math.sqrt(abs(normalized)) / (index + 1)
    return checksum


def _analyze_symbol(symbol, frame, load_factor):
    working = frame.sort_values("price_time").copy()
    working["close"] = pd.to_numeric(working["close"], errors="coerce")
    working = working.dropna(subset=["close"])
    if working.empty:
        return [], 0.0

    working["daily_return_pct"] = working["close"].pct_change() * 100
    working["ma20"] = working["close"].rolling(window=20, min_periods=1).mean()
    working["ma60"] = working["close"].rolling(window=60, min_periods=1).mean()
    working["volatility20"] = working["daily_return_pct"].rolling(window=20, min_periods=2).std()
    running_max = working["close"].cummax()
    working["drawdown_pct"] = (working["close"] / running_max - 1) * 100

    checksum = _visible_cpu_analysis(working["close"].tolist(), load_factor)
    indicators = []
    for _, row in working.iterrows():
        indicators.append(
            {
                "symbol": symbol,
                "price_time": row["price_time"],
                "close": _number(row["close"]),
                "daily_return_pct": _number(row["daily_return_pct"]),
                "ma20": _number(row["ma20"]),
                "ma60": _number(row["ma60"]),
                "volatility20": _number(row["volatility20"]),
                "drawdown_pct": _number(row["drawdown_pct"]),
            }
        )
    return indicators, checksum


def analyze_records(records, load_factor=1):
    started = time.perf_counter()
    if not records:
        return [], {"analysis_ms": 0.0, "symbols": []}

    frame = pd.DataFrame.from_records(records)
    all_indicators = []
    symbol_timings = []
    total_checksum = 0.0

    for symbol, symbol_frame in frame.groupby("symbol", sort=True):
        symbol_started = time.perf_counter()
        with profile_tags({"stage": "analysis", "symbol": symbol}):
            with obs_span(
                "stock.analysis.symbol",
                {
                    "stock.symbol": symbol,
                    "stock.rows": len(symbol_frame),
                    "analysis.load_factor": int(load_factor),
                },
            ) as active_span:
                indicators, checksum = _analyze_symbol(symbol, symbol_frame, load_factor)
                set_span_attributes(active_span, {"stock.indicator_rows": len(indicators)})
        elapsed_ms = (time.perf_counter() - symbol_started) * 1000
        total_checksum += checksum
        symbol_timings.append(
            {
                "symbol": symbol,
                "analysis_ms": round(elapsed_ms, 3),
                "indicator_rows": len(indicators),
            }
        )
        all_indicators.extend(indicators)

    return all_indicators, {
        "analysis_ms": round((time.perf_counter() - started) * 1000, 3),
        "symbols": symbol_timings,
        "checksum": round(total_checksum, 6),
    }

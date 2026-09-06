from __future__ import annotations

import logging
import time

from analysis import analyze_records
from observability_support import profile_tags, set_span_attributes
from observability_support import span as obs_span

from .config import StockSettings
from .domain import get_symbol_frame, records_from_frame
from .ports import MarketDataSource, StockRepository


def elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)


class StockPipeline:
    def __init__(self, settings: StockSettings, source: MarketDataSource, repository: StockRepository) -> None:
        self.settings = settings
        self.source = source
        self.repository = repository

    def run_once(self, load_factor: int | None = None, include_analysis: bool = True) -> dict[str, object]:
        load_factor = self.settings.analysis_load_factor if load_factor is None else int(load_factor)
        total_started = time.perf_counter()
        result: dict[str, object] = {
            "symbols": list(self.settings.tickers),
            "period": self.settings.period,
            "interval": self.settings.interval,
            "price_rows": 0,
            "indicator_rows": 0,
            "stage_timings_ms": {},
            "per_symbol": [],
        }
        timings: dict[str, float] = result["stage_timings_ms"]  # type: ignore[assignment]
        per_symbol: list[dict[str, object]] = result["per_symbol"]  # type: ignore[assignment]

        with obs_span(
            "stock.pipeline.run",
            {
                "stock.symbols": ",".join(self.settings.tickers),
                "stock.symbol_count": len(self.settings.tickers),
                "stock.period": self.settings.period,
                "stock.interval": self.settings.interval,
                "analysis.load_factor": load_factor,
            },
        ) as active_span:
            self.repository.assert_schema_current()

            started = time.perf_counter()
            with profile_tags({"stage": "download_prices"}), obs_span(
                "stock.download_prices",
                {
                    "stock.symbols": ",".join(self.settings.tickers),
                    "stock.symbol_count": len(self.settings.tickers),
                    "stock.period": self.settings.period,
                    "stock.interval": self.settings.interval,
                },
            ):
                data = self.source.download(self.settings.tickers, self.settings.period, self.settings.interval)
            timings["download_prices"] = elapsed_ms(started)

            all_records: list[dict[str, object]] = []
            normalize_total_ms = 0.0
            for symbol in self.settings.tickers:
                started = time.perf_counter()
                with profile_tags({"stage": "normalize_prices", "symbol": symbol}), obs_span(
                    "stock.normalize_prices", {"stock.symbol": symbol}
                ):
                    try:
                        frame = get_symbol_frame(data, symbol)
                    except Exception as exc:
                        logging.warning("unable to extract %s from market-data result: %s", symbol, exc)
                        continue
                    records = records_from_frame(symbol, frame)
                symbol_ms = elapsed_ms(started)
                normalize_total_ms += symbol_ms
                logging.info("prepared %s rows for %s", len(records), symbol)
                per_symbol.append({"symbol": symbol, "price_rows": len(records), "normalize_ms": symbol_ms})
                all_records.extend(records)
            timings["normalize_prices"] = round(normalize_total_ms, 3)

            started = time.perf_counter()
            count = self.repository.store_prices(all_records)
            timings["store_prices"] = elapsed_ms(started)
            result["price_rows"] = count

            if include_analysis:
                started = time.perf_counter()
                with profile_tags({"stage": "analysis"}), obs_span(
                    "stock.analysis", {"stock.rows": len(all_records), "analysis.load_factor": load_factor}
                ):
                    indicators, analysis_meta = analyze_records(all_records, load_factor=load_factor)
                timings["analysis"] = elapsed_ms(started)
                result["analysis"] = analysis_meta

                by_symbol = {str(item["symbol"]): item for item in per_symbol}
                for item in analysis_meta.get("symbols", []):
                    symbol = str(item["symbol"])
                    by_symbol.setdefault(symbol, {"symbol": symbol}).update(item)

                started = time.perf_counter()
                indicator_count = self.repository.store_indicators(indicators)
                timings["store_indicators"] = elapsed_ms(started)
                result["indicator_rows"] = indicator_count

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

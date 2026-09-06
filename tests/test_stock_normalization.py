from datetime import UTC

import pandas as pd
from fetch_prices import records_from_frame


def test_records_from_frame_normalizes_market_data():
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [102.0, 103.0],
            "Low": [99.0, 100.0],
            "Close": [101.5, 102.5],
            "Adj Close": [101.4, 102.4],
            "Volume": [1_000, 2_000],
        },
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
    )

    records = records_from_frame("AAPL", frame)

    assert len(records) == 2
    assert records[0]["symbol"] == "AAPL"
    assert records[0]["close"] == 101.5
    assert records[0]["adj_close"] == 101.4
    assert records[0]["volume"] == 1_000
    assert records[0]["price_time"].tzinfo == UTC


def test_records_from_frame_drops_rows_without_close():
    frame = pd.DataFrame(
        {"Close": [None, 100.0], "Volume": [1, 2]},
        index=pd.to_datetime(["2026-01-02", "2026-01-05"]),
    )

    records = records_from_frame("SPY", frame)

    assert len(records) == 1
    assert records[0]["close"] == 100.0

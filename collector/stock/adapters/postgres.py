from __future__ import annotations

import psycopg
from obs_platform.config import DatabaseSettings
from observability_support import profile_tags
from observability_support import span as obs_span
from tenacity import retry, stop_after_attempt, wait_exponential

EXPECTED_SCHEMA_REVISION = "0001_initial"

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


class PostgresStockRepository:
    def __init__(self, settings: DatabaseSettings | None = None) -> None:
        self.settings = settings or DatabaseSettings.from_env()

    @property
    def connect_kwargs(self) -> dict[str, object]:
        return self.settings.psycopg_kwargs()

    @retry(stop=stop_after_attempt(8), wait=wait_exponential(multiplier=1, min=1, max=30))
    def wait_until_ready(self) -> None:
        with psycopg.connect(**self.connect_kwargs) as conn:
            conn.execute("SELECT 1")

    def assert_schema_current(self) -> None:
        with psycopg.connect(**self.connect_kwargs) as conn, conn.cursor() as cur:
            try:
                cur.execute("SELECT version_num FROM alembic_version")
                row = cur.fetchone()
            except psycopg.Error as exc:
                raise RuntimeError("database is not migrated; run `alembic upgrade head` first") from exc
        if row is None or row[0] != EXPECTED_SCHEMA_REVISION:
            actual = None if row is None else row[0]
            raise RuntimeError(
                f"database schema revision {actual!r} does not match required {EXPECTED_SCHEMA_REVISION!r}"
            )

    def store_prices(self, records: list[dict[str, object]]) -> int:
        if not records:
            return 0
        with (
            profile_tags({"stage": "store_prices"}),
            obs_span("stock.store_prices", {"stock.rows": len(records)}),
            psycopg.connect(**self.connect_kwargs) as conn,
            conn.cursor() as cur,
        ):
            cur.executemany(UPSERT_PRICES_SQL, records)
            conn.commit()
        return len(records)

    def store_indicators(self, records: list[dict[str, object]]) -> int:
        if not records:
            return 0
        with (
            profile_tags({"stage": "store_indicators"}),
            obs_span("stock.store_indicators", {"stock.rows": len(records)}),
            psycopg.connect(**self.connect_kwargs) as conn,
            conn.cursor() as cur,
        ):
            cur.executemany(UPSERT_INDICATORS_SQL, records)
            conn.commit()
        return len(records)

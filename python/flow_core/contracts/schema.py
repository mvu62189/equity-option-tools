from __future__ import annotations

from enum import StrEnum

import polars as pl


class OptionType(StrEnum):
    CALL = "call"
    PUT = "put"


CANONICAL_COLUMNS = [
    "symbol",
    "contract_symbol",
    "asof_ts",
    "expiration",
    "option_type",
    "strike",
    "bid",
    "ask",
    "last",
    "volume",
    "open_interest",
    "underlying_price",
    "implied_vol_vendor",
    "provider",
    "snapshot_id",
]

REQUIRED_CANONICAL_COLUMNS = [
    "symbol",
    "contract_symbol",
    "asof_ts",
    "expiration",
    "option_type",
    "strike",
    "bid",
    "ask",
    "last",
    "volume",
    "open_interest",
    "underlying_price",
    "provider",
    "snapshot_id",
]

CANONICAL_DTYPES = {
    "symbol": pl.String,
    "contract_symbol": pl.String,
    "asof_ts": pl.Datetime(time_unit="us", time_zone="UTC"),
    "expiration": pl.Date,
    "option_type": pl.String,
    "strike": pl.Float64,
    "bid": pl.Float64,
    "ask": pl.Float64,
    "last": pl.Float64,
    "volume": pl.Int64,
    "open_interest": pl.Int64,
    "underlying_price": pl.Float64,
    "implied_vol_vendor": pl.Float64,
    "provider": pl.String,
    "snapshot_id": pl.String,
}

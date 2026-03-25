from __future__ import annotations

from datetime import datetime, timezone

import polars as pl

from flow_core.config.models import ProviderMap
from flow_core.ingestion.mapper import map_provider_records


def provider_map() -> ProviderMap:
    return ProviderMap(
        provider="yfinance",
        required_fields=[
            "contractSymbol",
            "strike",
            "bid",
            "ask",
            "volume",
            "openInterest",
            "lastPrice",
            "impliedVolatility",
            "expiration",
            "optionType",
            "asofTs",
            "underlyingPrice",
        ],
        field_map={
            "contractSymbol": "contract_symbol",
            "strike": "strike",
            "bid": "bid",
            "ask": "ask",
            "volume": "volume",
            "openInterest": "open_interest",
            "lastPrice": "last",
            "impliedVolatility": "implied_vol_vendor",
            "expiration": "expiration",
            "optionType": "option_type",
            "asofTs": "asof_ts",
            "underlyingPrice": "underlying_price",
        },
    )


def test_mapper_applies_mapping_and_types() -> None:
    records = pl.DataFrame(
        {
            "contractSymbol": ["SPY240621C00450000"],
            "strike": [450.0],
            "bid": [1.1],
            "ask": [1.2],
            "volume": [0],
            "openInterest": [100],
            "lastPrice": [1.15],
            "impliedVolatility": [0.22],
            "expiration": ["2026-06-21"],
            "optionType": ["CALL"],
            "asofTs": [datetime.now(timezone.utc)],
            "underlyingPrice": [449.5],
        }
    )

    out = map_provider_records(records, provider_map())
    assert out.height == 1
    assert out["option_type"][0] == "call"
    assert out["provider"][0] == "yfinance"
    assert out["contract_symbol"][0] == "SPY240621C00450000"


def test_drop_rule_only_bid_ask_zero_rows_removed() -> None:
    now = datetime.now(timezone.utc)
    records = pl.DataFrame(
        {
            "contractSymbol": ["A", "B", "C"],
            "strike": [100.0, 100.0, 100.0],
            "bid": [0.0, 0.5, 0.0],
            "ask": [0.0, 0.6, 0.1],
            "volume": [0, 0, 0],
            "openInterest": [1, 1, 1],
            "lastPrice": [0.0, 0.5, 0.1],
            "impliedVolatility": [0.2, 0.2, 0.2],
            "expiration": ["2026-06-21", "2026-06-21", "2026-06-21"],
            "optionType": ["call", "call", "call"],
            "asofTs": [now, now, now],
            "underlyingPrice": [100.0, 100.0, 100.0],
        }
    )

    out = map_provider_records(records, provider_map())
    assert out.height == 2
    assert set(out["contract_symbol"].to_list()) == {"B", "C"}


def test_mapper_fills_nullable_market_fields() -> None:
    now = datetime.now(timezone.utc)
    records = pl.DataFrame(
        {
            "contractSymbol": ["A", "B"],
            "strike": [100.0, 101.0],
            "bid": [0.5, None],
            "ask": [0.7, 0.8],
            "volume": [None, 5],
            "openInterest": [None, 11],
            "lastPrice": [None, None],
            "impliedVolatility": [0.2, 0.21],
            "expiration": ["2026-06-21", "2026-06-21"],
            "optionType": ["call", "put"],
            "asofTs": [now, now],
            "underlyingPrice": [100.0, 100.0],
        }
    )

    out = map_provider_records(records, provider_map())
    assert out.height == 2
    assert out["volume"].to_list() == [0, 5]
    assert out["open_interest"].to_list() == [0, 11]
    assert out["last"][0] == 0.6
    assert out["bid"][1] == 0.0

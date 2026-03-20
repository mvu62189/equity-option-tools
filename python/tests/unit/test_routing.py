from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl

from flow_core.quant.routing import annotate_with_routing, route_expiry_bucket


def test_route_expiry_bucket_matrix() -> None:
    assert route_expiry_bucket(1).greeks_engine == "crank_nicolson_fdm"
    assert route_expiry_bucket(10).greeks_engine == "binomial_richardson"
    assert route_expiry_bucket(40).iv_engine == "bjerksund_stensland"
    assert route_expiry_bucket(500).iv_engine == "luba_2point"
    assert route_expiry_bucket(1500).greeks_engine == "laplace_transform_zhu"


def test_annotate_with_routing_adds_dispatch_columns() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": [datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc)],
            "expiration": [date(2026, 2, 27)],
            "option_type": ["call"],
            "strike": [500.0],
            "bid": [1.0],
            "ask": [1.2],
            "last": [1.1],
            "volume": [10],
            "open_interest": [100],
            "underlying_price": [510.0],
            "implied_vol_vendor": [0.2],
            "provider": ["yfinance"],
            "snapshot_id": ["x"],
        }
    )
    out = annotate_with_routing(frame)
    assert "iv_engine" in out.columns
    assert "greeks_engine" in out.columns
    assert out["iv_engine"][0] == "bjerksund_stensland"

from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl
import pytest

from flow_core.quant.routed_greeks import ROUTED_GREEKS_COLUMNS, compute_routed_greeks


def test_compute_routed_greeks_returns_expected_columns() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["SPY", "SPY", "SPY"],
            "asof_ts": [
                datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
            ],
            "expiration": [date(2026, 2, 28), date(2026, 3, 20), date(2027, 3, 20)],
            "option_type": ["call", "put", "call"],
            "strike": [500.0, 500.0, 500.0],
            "underlying_price": [505.0, 505.0, 505.0],
            "implied_vol_vendor": [0.2, 0.2, 0.2],
            "greeks_engine": ["crank_nicolson_fdm", "binomial_richardson", "rim"],
            "days_to_expiry": [3, 24, 388],
        }
    )
    out = compute_routed_greeks(frame, rate=0.04, dividend=0.0, tree_steps=40, rim_nodes=24)
    assert out.height == 3
    assert out.columns == ROUTED_GREEKS_COLUMNS
    assert out["success"].dtype == pl.Boolean


def test_compute_routed_greeks_handles_unknown_engine() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": [datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc)],
            "expiration": [date(2026, 3, 20)],
            "option_type": ["call"],
            "strike": [500.0],
            "underlying_price": [505.0],
            "implied_vol_vendor": [0.2],
            "greeks_engine": ["unknown_engine"],
        }
    )
    out = compute_routed_greeks(frame)
    assert out.height == 1
    assert not out["success"][0]
    assert out["error"][0] == "unknown_engine"


def test_fdm_strict_mode_does_not_fallback_when_cpp_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": [datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc)],
            "expiration": [date(2026, 2, 28)],
            "option_type": ["call"],
            "strike": [500.0],
            "underlying_price": [505.0],
            "implied_vol_vendor": [0.2],
            "greeks_engine": ["crank_nicolson_fdm"],
            "days_to_expiry": [3],
        }
    )
    monkeypatch.setattr("flow_core.quant.routed_greeks.quantcore", None)
    out = compute_routed_greeks(frame, fdm_backend="cpp", runtime_mode="live_strict")
    assert out.height == 1
    assert out["engine_used"][0] == "fdm_cn_log_cpp"
    assert not out["success"][0]
    assert "strict" in out["error"][0]


def test_laplace_put_path_does_not_fallback_to_rim() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": [datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc)],
            "expiration": [date(2030, 3, 20)],
            "option_type": ["put"],
            "strike": [500.0],
            "underlying_price": [505.0],
            "implied_vol_vendor": [0.2],
            "greeks_engine": ["laplace_transform_zhu"],
            "days_to_expiry": [1480],
            "dividend_policy": ["escrowed"],
        }
    )
    out = compute_routed_greeks(frame, rate=0.04, dividend=0.0, laplace_m=12)
    assert out.height == 1
    assert out["engine_used"][0] in {"laplace_zhu", "laplace_zhu_cpp"}


@pytest.mark.skipif(
    __import__("importlib.util").util.find_spec("quantcore") is None,
    reason="quantcore module not built",
)
def test_bs2002_put_path_uses_cpp_when_available() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": [datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc)],
            "expiration": [date(2026, 9, 20)],
            "option_type": ["put"],
            "strike": [500.0],
            "underlying_price": [505.0],
            "implied_vol_vendor": [0.2],
            "greeks_engine": ["bjerksund_stensland"],
            "days_to_expiry": [210],
            "dividend_policy": ["escrowed"],
        }
    )
    out = compute_routed_greeks(frame, rate=0.04, dividend=0.0)
    assert out.height == 1
    assert out["engine_used"][0] == "bs2002_cpp"

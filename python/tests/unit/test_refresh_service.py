from __future__ import annotations

import polars as pl

from flow_core.orchestration.refresh_service import compare_refresh_frames


def _frame(**overrides) -> pl.DataFrame:
    row = {
        "symbol": "SPY",
        "expiration": "2026-03-20",
        "option_type": "call",
        "strike": 100.0,
        "bid": 1.0,
        "ask": 1.2,
        "last": 1.1,
        "underlying_price": 101.0,
        "implied_vol_vendor": 0.2,
        "volume": 10,
        "open_interest": 20,
    }
    row.update(overrides)
    return pl.DataFrame([row])


def test_compare_refresh_frames_detects_price_space_change() -> None:
    prev = _frame()
    cur = _frame(bid=1.05)
    diff = compare_refresh_frames(prev, cur, abs_tol=1e-4)
    assert diff.price_space_changed is True
    assert "bid" in diff.changed_fields
    assert diff.oi_only_changed is False


def test_compare_refresh_frames_detects_oi_only_change() -> None:
    prev = _frame()
    cur = _frame(open_interest=25, volume=12)
    diff = compare_refresh_frames(prev, cur, abs_tol=1e-4)
    assert diff.price_space_changed is False
    assert diff.oi_only_changed is True
    assert "open_interest" in diff.changed_fields


def test_compare_refresh_frames_detects_contract_set_change() -> None:
    prev = _frame()
    cur = pl.concat([_frame(), _frame(strike=105.0)], how="vertical")
    diff = compare_refresh_frames(prev, cur, abs_tol=1e-4)
    assert diff.price_space_changed is True
    assert "contract_set" in diff.changed_fields
    assert diff.added_contracts == 1

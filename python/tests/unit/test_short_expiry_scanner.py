from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl

from flow_core.orchestration.short_expiry_scanner import (
    build_short_expiry_scanner_bundle,
    resolve_focus_expirations,
)


def test_resolve_focus_expirations_returns_0dte_1dte_and_eow() -> None:
    expiries = ["2026-03-02", "2026-03-03", "2026-03-06", "2026-03-20"]
    resolved = resolve_focus_expirations(expiries, asof_date=date(2026, 3, 2), focus_labels=["0DTE", "1DTE", "EOW"])
    assert resolved == ["2026-03-02", "2026-03-03", "2026-03-06"]


def test_build_short_expiry_scanner_bundle_persists_summary_dealer_flow_and_levels() -> None:
    asof_ts = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
    raw = pl.DataFrame(
        {
            "symbol": ["SPY"] * 6,
            "asof_ts": [asof_ts] * 6,
            "batch_id": ["b1"] * 6,
            "expiration": ["2026-03-02", "2026-03-02", "2026-03-03", "2026-03-03", "2026-03-06", "2026-03-06"],
            "option_type": ["call", "put", "call", "put", "call", "put"],
            "strike": [590.0, 590.0, 595.0, 595.0, 600.0, 600.0],
            "volume": [120, 95, 140, 90, 220, 180],
            "open_interest": [450, 420, 480, 400, 620, 610],
        }
    ).with_columns(pl.col("expiration").str.to_date(format="%Y-%m-%d"))
    surface_points = pl.DataFrame(
        {
            "symbol": ["SPY"] * 6,
            "asof_ts": [asof_ts] * 6,
            "batch_id": ["b1"] * 6,
            "expiration": ["2026-03-02", "2026-03-02", "2026-03-03", "2026-03-03", "2026-03-06", "2026-03-06"],
            "option_type": ["call", "put", "call", "put", "call", "put"],
            "strike": [590.0, 590.0, 595.0, 595.0, 600.0, 600.0],
            "eligible": [True, True, True, False, True, True],
            "within_bid_ask": [True, True, True, False, True, True],
            "one_sided_market": [False, False, False, True, False, False],
            "strip_shape_fail": [False, False, False, True, False, False],
            "iv_ref": [0.18, 0.19, 0.185, 0.2, 0.19, 0.205],
            "market_mid": [6.4, 5.7, 5.8, 5.1, 5.0, 4.8],
            "atm_distance": [0.01, 0.01, 0.005, 0.005, 0.015, 0.015],
            "underlying_price": [593.0] * 6,
        }
    ).with_columns(pl.col("expiration").str.to_date(format="%Y-%m-%d"))
    greeks = pl.DataFrame(
        {
            "symbol": ["SPY"] * 6,
            "asof_ts": [asof_ts] * 6,
            "batch_id": ["b1"] * 6,
            "expiration": ["2026-03-02", "2026-03-02", "2026-03-03", "2026-03-03", "2026-03-06", "2026-03-06"],
            "option_type": ["call", "put", "call", "put", "call", "put"],
            "strike": [590.0, 590.0, 595.0, 595.0, 600.0, 600.0],
            "delta": [0.62, -0.38, 0.57, -0.41, 0.49, -0.47],
            "gamma": [0.018, 0.017, 0.016, 0.015, 0.014, 0.014],
            "vega": [0.11, 0.10, 0.12, 0.11, 0.13, 0.12],
            "underlying_price": [593.0] * 6,
        }
    ).with_columns(pl.col("expiration").str.to_date(format="%Y-%m-%d"))
    previous_dealer = pl.DataFrame(
        {
            "focus_label": ["0DTE"],
            "expiration": ["2026-03-02"],
            "option_type": ["call"],
            "strike": [590.0],
            "gamma_exposure_oi": [100000.0],
            "delta_exposure_oi": [50000.0],
            "vega_exposure_oi": [5000.0],
            "open_interest": [400],
            "volume": [100],
            "avg_market_mid": [6.0],
            "avg_iv_ref": [0.17],
        }
    ).with_columns(pl.col("expiration").str.to_date(format="%Y-%m-%d"))

    bundle = build_short_expiry_scanner_bundle(
        raw=raw,
        greeks=greeks,
        surface_points=surface_points,
        previous_dealer_exposure_points=previous_dealer,
        focus_labels=["0DTE", "1DTE", "EOW"],
        symbol="SPY",
        asof_ts=asof_ts,
        batch_id="b1",
        trading_date="2026-03-02",
        snapshot_kind="live_batch",
        source_mode="ui_live",
    )

    assert bundle.focus_expiry_summary.height == 3
    assert bundle.dealer_exposure_points.height >= 3
    assert bundle.flow_proxy_points.height >= 1
    assert bundle.scanner_levels.height >= 3
    assert set(bundle.focus_expiry_summary["focus_label"].to_list()) == {"0DTE", "1DTE", "EOW"}
    assert "trust_status" in bundle.focus_expiry_summary.columns
    assert "proxy_confidence" in bundle.flow_proxy_points.columns
    assert "hotspot_score" in bundle.scanner_levels.columns

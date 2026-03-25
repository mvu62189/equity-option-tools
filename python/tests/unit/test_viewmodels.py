from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import numpy as np
import polars as pl

from flow_core.orchestration.state_store import SymbolSnapshot
from flow_ui.viewmodels import (
    build_calendar_payload,
    build_density_payload,
    build_overlay_payload,
    build_price_error_payload,
    build_runtime_metrics_payload,
    build_short_expiry_scanner_payload,
    build_surface_validation_payload,
)


def _snapshot() -> SymbolSnapshot:
    greeks = pl.DataFrame(
        {
            "expiration": ["2026-03-20", "2026-03-20", "2026-04-17", "2026-04-17"],
            "option_type": ["call", "call", "call", "call"],
            "engine_used": ["laplace_zhu_cpp", "laplace_zhu_cpp", "laplace_zhu_cpp", "laplace_zhu_cpp"],
            "strike": [90.0, 100.0, 90.0, 100.0],
            "delta": [0.6, 0.5, 0.55, 0.45],
        }
    )
    return SymbolSnapshot(
        symbol="SPY",
        batch_id="b1",
        version=1,
        updated_at_utc=datetime.now(timezone.utc),
        raw=pl.DataFrame(),
        greeks=greeks,
        ssvi=pl.DataFrame(),
        dispatch=pl.DataFrame(),
        parity=pl.DataFrame(),
        parity_detail=pl.DataFrame(),
        calibration_diag_tail=pl.DataFrame(),
        overlay_payloads={},
        memory_bytes={},
        drop_counters={},
        latency_ms={},
        status={},
    )


def test_overlay_payload_is_contiguous_float32() -> None:
    payload = build_overlay_payload(_snapshot(), greek="delta", option_type="call", expiry_filter="all")
    heat = payload["heat_image"]
    assert isinstance(heat, np.ndarray)
    assert heat.dtype == np.float32
    assert heat.flags["C_CONTIGUOUS"]
    assert heat.ndim == 2
    assert payload["meta"]["status"] == "ok"


def test_overlay_payload_supports_space_mode_and_engine_mask() -> None:
    payload = build_overlay_payload(
        _snapshot(),
        greek="delta",
        option_type="call",
        expiry_filter="all",
        space_mode="log",
        engine_mask={"laplace_zhu_cpp"},
        dual_mode=True,
    )
    assert payload["meta"]["space_mode"] == "log"
    assert payload["meta"]["payload_bytes"] >= payload["heat_image"].nbytes
    assert "heat_image_secondary" in payload


def test_price_error_payload_uses_routed_greeks_prices() -> None:
    snapshot = _snapshot()
    snapshot = replace(
        snapshot,
        greeks=snapshot.greeks.with_columns(
            pl.Series("model_price", [1.1, 1.0, 1.2, 1.1]),
            pl.Series("market_mid", [1.0, 0.9, 1.15, 1.05]),
            pl.Series("market_bid", [0.95, 0.85, 1.10, 1.00]),
            pl.Series("market_ask", [1.05, 0.95, 1.20, 1.10]),
        ),
    )
    payload = build_price_error_payload(snapshot, option_type="call", expiry_filter="2026-03-20", relative=False)
    assert payload["meta"]["status"] == "ok"
    assert "call:laplace_zhu_cpp:model_price" in payload["line_series"]
    assert "call:laplace_zhu_cpp:corridor_error" in payload["error_series"]


def test_price_error_payload_uses_surface_points_american_model_price() -> None:
    surface_points = pl.DataFrame(
        {
            "asof_ts": [datetime(2026, 3, 1, tzinfo=timezone.utc)] * 4,
            "batch_id": ["b2"] * 4,
            "expiration": ["2026-03-20"] * 4,
            "option_type": ["call", "call", "put", "put"],
            "strike": [95.0, 100.0, 95.0, 100.0],
            "american_model_price": [6.2, 3.8, 1.4, 3.1],
            "market_mid": [6.0, 3.7, 1.3, 3.0],
            "bid": [5.9, 3.6, 1.2, 2.9],
            "ask": [6.1, 3.8, 1.4, 3.1],
        }
    )
    payload = build_price_error_payload(surface_points, option_type="all", expiry_filter="2026-03-20", relative=False)
    assert payload["meta"]["status"] == "ok"
    assert payload["meta"]["data_source"] == "surface_points"
    assert "call:model_price" in payload["line_series"]
    assert "put:model_price" in payload["line_series"]
    assert "call:corridor_error" in payload["error_series"]


def test_validation_calendar_density_and_runtime_metric_payloads() -> None:
    surface_points = pl.DataFrame(
        {
            "asof_ts": [datetime(2026, 3, 1, tzinfo=timezone.utc)] * 6,
            "batch_id": ["b1"] * 6,
            "expiration": ["2026-03-20"] * 3 + ["2026-04-17"] * 3,
            "option_type": ["call"] * 6,
            "strike": [90.0, 100.0, 110.0, 90.0, 100.0, 110.0],
            "days_to_expiry": [19, 19, 19, 47, 47, 47],
            "implied_vol": [0.24, 0.21, 0.2, 0.26, 0.22, 0.21],
            "iv_bid": [0.235, 0.205, 0.195, 0.255, 0.215, 0.205],
            "iv_ask": [0.245, 0.215, 0.205, 0.265, 0.225, 0.215],
            "iv_ref": [0.238, 0.208, 0.198, 0.258, 0.218, 0.208],
            "vendor_iv_ref": [0.24, 0.21, 0.20, 0.26, 0.22, 0.21],
            "model_implied_vol": [0.235, 0.208, 0.202, 0.255, 0.221, 0.212],
            "bid": [11.8, 7.3, 3.9, 12.8, 7.8, 4.5],
            "ask": [12.2, 7.7, 4.1, 13.2, 8.2, 4.7],
            "market_mid": [12.0, 7.5, 4.0, 13.0, 8.0, 4.6],
            "model_price": [12.1, 7.4, 4.1, 13.1, 8.1, 4.7],
            "american_model_price": [12.1, 7.4, 4.1, 13.1, 8.1, 4.7],
            "delta": [0.72, 0.51, 0.31, 0.7, 0.5, 0.33],
            "gamma": [0.018, 0.021, 0.015, 0.016, 0.019, 0.014],
            "theta": [-0.05, -0.04, -0.03, -0.06, -0.045, -0.032],
            "vega": [0.12, 0.15, 0.11, 0.13, 0.16, 0.12],
            "rho": [0.08, 0.06, 0.04, 0.09, 0.07, 0.05],
            "dual_delta_bid": [0.72, 0.50, 0.28, 0.70, 0.49, 0.30],
            "dual_delta_ask": [0.74, 0.52, 0.30, 0.72, 0.51, 0.32],
            "dual_delta_ref": [0.73, 0.51, 0.29, 0.71, 0.50, 0.31],
            "price_second_derivative_ref": [0.02, 0.03, 0.02, 0.018, 0.028, 0.019],
            "price_error_abs": [0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
            "price_error_rel": [0.008, 0.013, 0.025, 0.008, 0.012, 0.022],
            "vol_error_abs": [0.005, 0.002, 0.002, 0.005, 0.001, 0.002],
            "vol_error_rel": [0.021, 0.01, 0.01, 0.019, 0.005, 0.01],
            "calendar_total_variance": [0.08, 0.07, 0.065, 0.11, 0.095, 0.09],
            "calendar_violation": [False, False, False, False, False, True],
        }
    )

    validation = build_surface_validation_payload(
        surface_points,
        metric="implied_vol",
        option_type="call",
        expiry_filter="2026-03-20",
    )
    assert validation["meta"]["status"] == "ok"
    assert validation["meta"]["selected_expiry"] == "2026-03-20"
    assert "call:iv_ref" in validation["line_series"]
    assert "call:model_implied_vol" in validation["line_series"]
    assert validation["heat_image"].dtype == np.float32

    calendar = build_calendar_payload(surface_points, option_type="call")
    assert calendar["meta"]["status"] == "ok"
    assert calendar["meta"]["violation_count"] == 1

    density = build_density_payload(surface_points, option_type="call", expiry_filter="2026-03-20")
    assert density["meta"]["status"] == "ok"
    assert "density" in density["line_series"]

    runtime_metrics = pl.DataFrame(
        {
            "asof_ts": [
                datetime(2026, 3, 1, 14, 0, tzinfo=timezone.utc),
                datetime(2026, 3, 1, 14, 1, tzinfo=timezone.utc),
                datetime(2026, 3, 1, 14, 2, tzinfo=timezone.utc),
            ],
            "version": [1, 2, 3],
            "total_ms": [12.0, 10.0, 9.0],
            "calibration_ms": [4.0, 3.5, 3.0],
            "pricing_ms": [5.0, 4.0, 3.8],
            "routing_ms": [1.0, 0.9, 0.8],
        }
    )
    runtime = build_runtime_metrics_payload(runtime_metrics)
    assert runtime["meta"]["status"] == "ok"
    assert runtime["meta"]["latest_total_ms"] == 9.0
    assert {"total_ms", "calibration_ms", "pricing_ms", "routing_ms"} <= set(runtime["line_series"])


def test_short_expiry_scanner_payload_builds_summary_and_levels() -> None:
    focus_summary = pl.DataFrame(
        {
            "asof_ts": [datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc)] * 3,
            "batch_id": ["b3"] * 3,
            "focus_label": ["0DTE", "1DTE", "EOW"],
            "focus_order": [0, 1, 2],
            "expiration": ["2026-03-01", "2026-03-02", "2026-03-06"],
            "trust_status": ["trusted", "review", "caution"],
            "trust_score": [88.0, 71.0, 58.0],
            "snapshot_age_sec": [4.0, 4.0, 4.0],
        }
    ).with_columns(pl.col("expiration").str.to_date("%Y-%m-%d"))
    dealer_points = pl.DataFrame(
        {
            "asof_ts": [datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc)] * 4,
            "batch_id": ["b3"] * 4,
            "focus_label": ["0DTE", "0DTE", "1DTE", "EOW"],
            "strike": [595.0, 600.0, 600.0, 605.0],
            "gamma_exposure_oi": [1000.0, 1800.0, 900.0, 600.0],
        }
    )
    scanner_levels = pl.DataFrame(
        {
            "asof_ts": [datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc)] * 2,
            "batch_id": ["b3"] * 2,
            "focus_label": ["0DTE", "0DTE"],
            "expiration": ["2026-03-01", "2026-03-01"],
            "strike": [595.0, 600.0],
            "hotspot_score": [4.0, 5.0],
            "avg_iv_ref": [0.18, 0.19],
            "avg_market_mid": [4.2, 3.7],
            "total_volume": [800, 950],
            "total_open_interest": [1200, 1600],
            "net_gamma_exposure_oi": [1000.0, 1800.0],
            "eligible_ratio": [0.9, 0.95],
            "within_bid_ask_ratio": [0.92, 0.94],
        }
    ).with_columns(pl.col("expiration").str.to_date("%Y-%m-%d"))
    flow_proxy_points = pl.DataFrame(
        {
            "asof_ts": [datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc)],
            "batch_id": ["b3"],
            "focus_label": ["0DTE"],
            "expiration": ["2026-03-01"],
            "option_type": ["call"],
            "strike": [600.0],
            "delta_volume": [125],
            "delta_open_interest": [60],
            "delta_avg_market_mid": [0.12],
            "delta_avg_iv_ref": [0.01],
            "delta_gamma_exposure_oi": [250.0],
            "proxy_confidence": [0.65],
            "proxy_reason": ["snapshot_delta_proxy_not_trade_tape"],
        }
    ).with_columns(pl.col("expiration").str.to_date("%Y-%m-%d"))

    payload = build_short_expiry_scanner_payload(
        focus_summary,
        dealer_points,
        scanner_levels,
        flow_proxy_points,
        selected_focus_label="0DTE",
    )

    assert payload["meta"]["status"] == "ok"
    assert payload["meta"]["selected_focus_label"] == "0DTE"
    assert payload["meta"]["selected_expiration"] == "2026-03-01"
    assert payload["summary_frame"].height == 3
    assert payload["levels_frame"].height == 2
    assert payload["flow_frame"].height == 1
    assert payload["heat_image"].dtype == np.float32

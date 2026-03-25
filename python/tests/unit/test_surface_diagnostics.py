from __future__ import annotations

from datetime import datetime, timezone
import math

import polars as pl

from flow_core.orchestration.surface_diagnostics import (
    build_surface_batch_summary,
    build_surface_diagnostics,
    build_surface_point_diagnostics,
)
from flow_core.quant.bs import price_euro_bs
from flow_core.quant.models import BSInput


def _make_surface_frame() -> pl.DataFrame:
    spot = 100.0
    rate = 0.02
    dividend = 0.0
    expiries = [
        (datetime(2026, 4, 1, tzinfo=timezone.utc), 0.25),
        (datetime(2026, 7, 1, tzinfo=timezone.utc), 0.75),
    ]
    strikes = [70.0, 100.0, 130.0]
    vols = {
        (0, 70.0): 0.30,
        (0, 100.0): 0.22,
        (0, 130.0): 0.18,
        (1, 70.0): 0.18,
        (1, 100.0): 0.10,
        (1, 130.0): 0.14,
    }
    deltas = {
        (0, 70.0): 0.92,
        (0, 100.0): 0.72,
        (0, 130.0): 0.50,
        (1, 70.0): 0.89,
        (1, 100.0): 0.94,
        (1, 130.0): 0.53,
    }
    gammas = {
        (0, 70.0): 0.02,
        (0, 100.0): 0.03,
        (0, 130.0): 0.01,
        (1, 70.0): 0.02,
        (1, 100.0): -0.01,
        (1, 130.0): 0.01,
    }
    rows: list[dict[str, object]] = []
    for expiry_idx, (expiration, tau) in enumerate(expiries):
        for strike in strikes:
            vol = vols[(expiry_idx, strike)]
            model_price = float(
                price_euro_bs(
                    BSInput(
                        spot=spot,
                        strike=strike,
                        rate=rate,
                        dividend=dividend,
                        tau=tau,
                        vol=vol,
                        is_call=True,
                    )
                ).price
            )
            rows.append(
                {
                    "symbol": "SPY",
                    "asof_ts": datetime(2026, 1, 2, tzinfo=timezone.utc),
                    "expiration": expiration,
                    "option_type": "call",
                    "strike": strike,
                    "underlying_price": spot,
                    "tau_years": tau,
                    "bid": model_price - 0.10,
                    "ask": model_price + 0.10,
                    "market_mid": model_price - 0.03,
                    "model_price": model_price,
                    "implied_vol_vendor": vol + (0.02 if (expiry_idx == 0 and strike == 70.0) else 0.0),
                    "rate_used": rate,
                    "dividend_used": dividend,
                    "delta": deltas[(expiry_idx, strike)],
                    "gamma": gammas[(expiry_idx, strike)],
                    "success": not (expiry_idx == 1 and strike == 100.0),
                    "backend_used": "cpp",
                    "runtime_mode": "live_strict",
                }
            )
    return pl.DataFrame(rows)


def test_surface_diagnostics_adds_point_and_summary_metrics() -> None:
    frame = _make_surface_frame()
    points = build_surface_point_diagnostics(frame)
    summary = build_surface_batch_summary(frame, point_diagnostics=points)
    bundle = build_surface_diagnostics(frame)

    assert bundle.points.height == frame.height
    assert bundle.summary.height == 1
    assert points.columns == bundle.points.columns
    assert summary.columns == bundle.summary.columns

    row = points.filter(pl.col("strike") == 100.0).sort("expiration").to_dicts()[0]
    assert math.isclose(float(row["model_implied_vol"]), 0.22, rel_tol=1e-3)
    assert math.isclose(float(row["price_error_abs"]), 0.03, rel_tol=1e-6)
    assert bool(row["within_bid_ask"]) is True
    assert math.isclose(float(row["log_moneyness"]), 0.0, abs_tol=1e-12)
    assert math.isclose(float(row["atm_distance"]), 0.0, abs_tol=1e-12)

    later_row = points.filter((pl.col("expiration") == datetime(2026, 7, 1, tzinfo=timezone.utc)) & (pl.col("strike") == 100.0)).to_dicts()[0]
    assert bool(later_row["is_negative_gamma"]) is True
    assert bool(later_row["delta_smoothness_violation"]) is True
    assert bool(later_row["calendar_violation"]) is True

    stats = summary.to_dicts()[0]
    assert stats["rows"] == 6
    assert stats["groups"] == 6
    assert stats["failure_count"] == 1
    assert math.isclose(float(stats["model_implied_vol_coverage"]), 1.0, abs_tol=1e-12)
    assert math.isclose(float(stats["within_bid_ask_ratio"]), 1.0, abs_tol=1e-12)
    assert stats["negative_gamma_count"] == 1
    assert stats["delta_smoothness_violation_count"] == 1
    assert stats["calendar_violation_count"] == 1
    assert math.isclose(float(stats["price_rmse"]), 0.03, rel_tol=1e-6)
    assert float(stats["vol_rmse"]) > 0.0
    assert float(stats["atm_mae"]) < 1e-6
    assert float(stats["wing_rmse"]) > 0.0


def test_surface_diagnostics_handles_empty_frames() -> None:
    empty = pl.DataFrame()
    points = build_surface_point_diagnostics(empty)
    summary = build_surface_batch_summary(empty)

    assert points.is_empty()
    assert summary.height == 0

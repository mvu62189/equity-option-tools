from __future__ import annotations

import math

import polars as pl

from flow_core.quant.ssvi import calibrate_ssvi


def _sample_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "strike": [90.0, 95.0, 100.0, 105.0, 110.0],
            "implied_vol_vendor": [0.25, 0.22, 0.20, 0.21, 0.24],
            "underlying_price": [100.0, 100.0, 100.0, 100.0, 100.0],
            "bid": [1.0, 1.1, 1.2, 1.1, 1.0],
            "ask": [1.2, 1.3, 1.4, 1.3, 1.2],
        }
    )


def test_ssvi_calibration_enforces_valid_parameter_region() -> None:
    frame = _sample_frame()
    result = calibrate_ssvi(frame)
    assert result.success
    assert -0.999 <= result.rho <= 0.999
    assert result.b > 0
    assert result.sigma > 0


def test_ssvi_supports_log_and_strike_fit_spaces() -> None:
    frame = _sample_frame()
    log_result = calibrate_ssvi(frame, fit_space="log")
    strike_result = calibrate_ssvi(frame, fit_space="strike")

    assert log_result.success
    assert strike_result.success
    assert math.isfinite(log_result.objective)
    assert math.isfinite(strike_result.objective)

from __future__ import annotations

import math
from datetime import date, datetime, timezone

import polars as pl
import pytest

from flow_core.quant.fdm_cn import price_greeks_crank_nicolson
from flow_core.quant.models import AmericanContract
from flow_core.quant.o4o5_engines import BjerksundStenslandEngine
from flow_core.quant.ssvi import calibrate_ssvi, calibrate_ssvi_cpp, ssvi_implied_vol_at


def _ssvi_corridor_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "strike": [80.0, 85.0, 90.0, 95.0, 100.0, 105.0, 110.0, 115.0, 120.0],
            "implied_vol_input": [0.31, 0.285, 0.255, 0.225, 0.205, 0.212, 0.226, 0.247, 0.275],
            "iv_bid": [0.30, 0.275, 0.246, 0.217, 0.198, 0.205, 0.219, 0.239, 0.267],
            "iv_ask": [0.32, 0.295, 0.264, 0.233, 0.212, 0.219, 0.234, 0.255, 0.283],
            "underlying_price": [100.0] * 9,
            "bid": [21.0, 16.4, 12.4, 8.8, 5.9, 3.8, 2.2, 1.2, 0.65],
            "ask": [21.8, 17.0, 12.9, 9.2, 6.2, 4.0, 2.35, 1.32, 0.74],
            "weight_atm": [0.6, 0.8, 1.1, 1.35, 1.6, 1.35, 1.1, 0.8, 0.6],
            "expiration": [date(2026, 4, 17)] * 9,
            "asof_ts": [datetime(2026, 3, 27, 15, 30, tzinfo=timezone.utc)] * 9,
        }
    )


def _tau_from_frame(frame: pl.DataFrame) -> float:
    expiration = frame["expiration"][0]
    asof_ts = frame["asof_ts"][0]
    exp_dt = datetime(expiration.year, expiration.month, expiration.day, 16, 0, 0, tzinfo=timezone.utc)
    seconds = max((exp_dt - asof_ts).total_seconds(), 60.0)
    return max(seconds / (365.25 * 24.0 * 3600.0), 1.0 / 365.0)


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
@pytest.mark.parametrize("fit_space", ["log", "strike"])
def test_cpp_ssvi_tracks_python_corridor_fit(fit_space: str) -> None:
    frame = _ssvi_corridor_frame()
    py_result = calibrate_ssvi(frame, fit_space=fit_space, vol_col="implied_vol_input", weight_col="weight_atm")
    cpp_result, meta = calibrate_ssvi_cpp(
        frame,
        fit_space=fit_space,
        vol_col="implied_vol_input",
        weight_col="weight_atm",
    )

    assert py_result.success
    assert cpp_result.success
    assert meta["fit_space"] == fit_space

    tau = _tau_from_frame(frame)
    strikes = frame["strike"].to_list()
    py_vols = [
        ssvi_implied_vol_at(
            strike=float(strike),
            spot=100.0,
            tau=tau,
            rate=0.0,
            dividend=0.0,
            params=py_result,
            fit_space=fit_space,
        )
        for strike in strikes
    ]
    cpp_vols = [
        ssvi_implied_vol_at(
            strike=float(strike),
            spot=100.0,
            tau=tau,
            rate=0.0,
            dividend=0.0,
            params=cpp_result,
            fit_space=fit_space,
        )
        for strike in strikes
    ]

    max_diff = max(abs(a - b) for a, b in zip(py_vols, cpp_vols, strict=True))
    assert max_diff < 0.012
    assert math.isfinite(cpp_result.objective)
    assert abs(cpp_result.objective - py_result.objective) < 0.05


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_cpp_fdm_log_tracks_python_reference() -> None:
    import quantcore  # type: ignore

    contract = AmericanContract(
        spot=100.0,
        strike=102.0,
        rate=0.04,
        dividend=0.0,
        tau=0.22,
        is_call=True,
    )
    cpp = quantcore.fdm_cn_log_greeks(
        float(contract.spot),
        float(contract.strike),
        float(contract.tau),
        float(contract.rate),
        float(contract.dividend),
        0.21,
        True,
        200,
        220,
        [],
    )
    py = price_greeks_crank_nicolson(
        contract,
        vol=0.21,
        s_steps=200,
        t_steps=220,
        divs=tuple(),
        scheme="log",
    )

    assert bool(cpp["success"]) is True
    assert py.success is True
    assert math.isclose(float(cpp["price"]), float(py.price), rel_tol=2e-3, abs_tol=2e-3)
    assert math.isclose(float(cpp["delta"]), float(py.delta), rel_tol=1e-2, abs_tol=2e-3)
    assert math.isclose(float(cpp["gamma"]), float(py.gamma), rel_tol=2e-2, abs_tol=2e-3)
    assert math.isclose(float(cpp["theta"]), float(py.theta), rel_tol=2e-2, abs_tol=3e-3)


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_bjerksund_engine_put_path_uses_compiled_contract_symmetrically() -> None:
    import quantcore  # type: ignore

    contract = AmericanContract(
        spot=95.0,
        strike=100.0,
        rate=0.04,
        dividend=0.0,
        tau=0.35,
        is_call=False,
    )
    engine = BjerksundStenslandEngine(steps=100)
    market_price = float(
        quantcore.bs2002_escrowed_put(
            float(contract.spot),
            float(contract.strike),
            float(contract.tau),
            float(contract.rate),
            0.26,
            [],
        )
    )
    diag = engine.estimate_eep(market_price=market_price, contract=contract)

    assert diag.success
    assert math.isfinite(diag.implied_vol)
    assert diag.implied_vol > 0.0
    assert math.isclose(diag.american_price, market_price, rel_tol=1e-4, abs_tol=1e-4)

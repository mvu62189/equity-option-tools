from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_bs2002_greeks_shape_and_finite() -> None:
    import quantcore  # type: ignore

    out = quantcore.bs2002_greeks_call(
        100.0,
        100.0,
        0.5,
        0.04,
        0.2,
        [(0.8, 0.25), (0.8, 0.45)],
    )
    assert isinstance(out, tuple)
    assert len(out) == 6
    assert all(math.isfinite(float(x)) for x in out)

    out_put = quantcore.bs2002_greeks_put(
        95.0,
        100.0,
        0.5,
        0.04,
        0.25,
        [(0.8, 0.25), (0.8, 0.45)],
    )
    assert isinstance(out_put, tuple)
    assert len(out_put) == 6
    assert all(math.isfinite(float(x)) for x in out_put)


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_bs2002_greeks_near_expiry_boundary_rule() -> None:
    import quantcore  # type: ignore

    s = 101.0
    k = 100.0
    price, delta, gamma, theta, vega, rho = quantcore.bs2002_greeks_call(s, k, 1e-6, 0.04, 0.2, [])
    assert abs(price - max(s - k, 0.0)) < 1e-12
    assert delta in {0.0, 1.0}
    assert gamma == 0.0
    assert theta == 0.0
    assert vega == 0.0
    assert rho == 0.0

    s_put = 99.0
    k_put = 100.0
    p_price, p_delta, p_gamma, p_theta, p_vega, p_rho = quantcore.bs2002_greeks_put(s_put, k_put, 1e-6, 0.04, 0.2, [])
    assert abs(p_price - max(k_put - s_put, 0.0)) < 1e-12
    assert p_delta in {-1.0, 0.0}
    assert p_gamma == 0.0
    assert p_theta == 0.0
    assert p_vega == 0.0
    assert p_rho == 0.0


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_bs2002_greeks_theta_day_shift_dividend_drop_is_finite() -> None:
    import quantcore  # type: ignore

    # Dividend occurs inside one day horizon and should drop from the T-dT theta leg.
    out = quantcore.bs2002_greeks_call(
        100.0,
        100.0,
        0.05,
        0.04,
        0.25,
        [(0.5, 0.5 / 365.0)],
    )
    theta = float(out[3])
    assert math.isfinite(theta)


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_bs2002_greeks_threadsafe_calls() -> None:
    import quantcore  # type: ignore

    def one_call() -> float:
        out = quantcore.bs2002_greeks_call(100.0, 100.0, 0.5, 0.04, 0.2, [(0.4, 0.2)])
        return float(out[0])

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: one_call(), range(64)))

    assert all(math.isfinite(x) and x >= 0.0 for x in results)

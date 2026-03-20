from __future__ import annotations

import math

import pytest

from flow_core.quant.laplace_zhu import price_laplace_zhu_call, price_laplace_zhu_put


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_cpp_laplace_prices_are_finite() -> None:
    import quantcore  # type: ignore

    call = float(quantcore.laplace_zhu_call(100.0, 100.0, 3.5, 0.04, 0.02, 0.2, 12))
    put = float(quantcore.laplace_zhu_put(100.0, 100.0, 3.5, 0.04, 0.02, 0.2, 12))
    assert math.isfinite(call) and call >= 0.0
    assert math.isfinite(put) and put >= 0.0


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_cpp_laplace_tracks_python_reference() -> None:
    import quantcore  # type: ignore

    spots = [80.0, 100.0, 120.0]
    strikes = [90.0, 100.0, 110.0]
    taus = [2.0, 3.5, 5.0]
    rates = [0.02, 0.04]
    divs = [0.0, 0.02]
    vols = [0.15, 0.25]

    for s in spots:
        for k in strikes:
            for tau in taus:
                for r in rates:
                    for q in divs:
                        for sigma in vols:
                            cpp_call = float(quantcore.laplace_zhu_call(s, k, tau, r, q, sigma, 12))
                            py_call = float(price_laplace_zhu_call(s, k, tau, r, q, sigma, m=12))
                            cpp_put = float(quantcore.laplace_zhu_put(s, k, tau, r, q, sigma, 12))
                            py_put = float(price_laplace_zhu_put(s, k, tau, r, q, sigma, m=12))
                            assert math.isclose(cpp_call, py_call, rel_tol=1e-6, abs_tol=1e-7)
                            assert math.isclose(cpp_put, py_put, rel_tol=1e-6, abs_tol=1e-7)


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_cpp_laplace_vega_rho_tuple() -> None:
    import quantcore  # type: ignore

    c_price, c_vega, c_rho = quantcore.laplace_zhu_call_vega_rho(100.0, 100.0, 3.5, 0.04, 0.02, 0.2, 12)
    p_price, p_vega, p_rho = quantcore.laplace_zhu_put_vega_rho(100.0, 100.0, 3.5, 0.04, 0.02, 0.2, 12)
    assert all(math.isfinite(float(x)) for x in (c_price, c_vega, c_rho, p_price, p_vega, p_rho))

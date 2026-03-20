from __future__ import annotations

import math

import pytest


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_bs2002_escrowed_call_basic_finite() -> None:
    import quantcore  # type: ignore

    price = float(
        quantcore.bs2002_escrowed_call(
            100.0,
            100.0,
            0.5,
            0.04,
            0.2,
            [(0.6, 0.2), (0.6, 0.45)],
        )
    )
    assert math.isfinite(price)
    assert price >= 0.0


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_bs2002_escrowed_call_near_expiry_returns_intrinsic() -> None:
    import quantcore  # type: ignore

    s = 105.0
    k = 100.0
    price = float(quantcore.bs2002_escrowed_call(s, k, 1e-8, 0.04, 0.2, []))
    assert abs(price - max(s - k, 0.0)) < 1e-12


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_bs2002_negative_adjusted_spot_guard() -> None:
    import quantcore  # type: ignore

    s = 10.0
    k = 8.0
    price = float(quantcore.bs2002_escrowed_call(s, k, 0.5, 0.04, 0.2, [(50.0, 0.25)]))
    assert abs(price - max(s - k, 0.0)) < 1e-12


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_bs2002_escrowed_put_basic_finite() -> None:
    import quantcore  # type: ignore

    s = 95.0
    k = 100.0
    price = float(quantcore.bs2002_escrowed_put(s, k, 0.5, 0.04, 0.25, [(0.6, 0.2)]))
    assert math.isfinite(price)
    assert price >= max(k - s, 0.0)

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest


pytest.importorskip("numpy")


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_cpp_binding_threadsafe_calls() -> None:
    import quantcore  # type: ignore

    def one_call() -> float:
        return float(quantcore.price_bs(450.0, 450.0, 0.04, 0.0, 0.1, 0.2, True))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: one_call(), range(64)))

    assert all(r > 0 for r in results)


@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_cpp_calibrate_ssvi_returns_metadata_dict() -> None:
    import quantcore  # type: ignore

    out = quantcore.calibrate_ssvi(
        [90.0, 95.0, 100.0, 105.0, 110.0],
        [0.25, 0.22, 0.20, 0.21, 0.24],
        {"a": 0.01, "b": 0.1, "rho": -0.2, "m": 0.0, "sigma": 0.25},
    )

    assert isinstance(out, dict)
    assert {"params", "iterations", "sse", "converged", "durrleman"} <= set(out.keys())
    assert isinstance(out["params"], list)

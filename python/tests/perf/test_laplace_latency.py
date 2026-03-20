from __future__ import annotations

import random
import statistics
import time

import pytest

from flow_core.quant.laplace_zhu import price_laplace_zhu_call, price_laplace_zhu_put


def _sample_params(n: int, seed: int = 7) -> list[tuple[float, float, float, float, float, float, bool]]:
    rng = random.Random(seed)
    rows: list[tuple[float, float, float, float, float, float, bool]] = []
    for i in range(n):
        rows.append(
            (
                80.0 + rng.random() * 60.0,  # s
                80.0 + rng.random() * 60.0,  # k
                1.5 + rng.random() * 4.5,  # tau
                0.01 + rng.random() * 0.05,  # r
                rng.random() * 0.03,  # q
                0.1 + rng.random() * 0.35,  # sigma
                i % 2 == 0,  # is_call
            )
        )
    return rows


def _pct(vals: list[float], p: int) -> float:
    # quantiles(..., n=100) gives centiles; index p-1 is pth percentile.
    return statistics.quantiles(vals, n=100)[p - 1]


@pytest.mark.perf
@pytest.mark.skipif(__import__("importlib.util").util.find_spec("quantcore") is None, reason="quantcore module not built")
def test_laplace_cpp_vs_python_latency_percentiles() -> None:
    import quantcore  # type: ignore

    params = _sample_params(300)
    py_lat: list[float] = []
    cpp_lat: list[float] = []

    for s, k, tau, r, q, sigma, is_call in params:
        t0 = time.perf_counter()
        if is_call:
            price_laplace_zhu_call(s, k, tau, r, q, sigma, m=12)
        else:
            price_laplace_zhu_put(s, k, tau, r, q, sigma, m=12)
        py_lat.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        if is_call:
            quantcore.laplace_zhu_call(s, k, tau, r, q, sigma, 12)
        else:
            quantcore.laplace_zhu_put(s, k, tau, r, q, sigma, 12)
        cpp_lat.append(time.perf_counter() - t0)

    py_p50 = _pct(py_lat, 50)
    py_p75 = _pct(py_lat, 75)
    py_p95 = _pct(py_lat, 95)
    cpp_p50 = _pct(cpp_lat, 50)
    cpp_p75 = _pct(cpp_lat, 75)
    cpp_p95 = _pct(cpp_lat, 95)

    assert cpp_p50 < py_p50, f"p50: cpp={cpp_p50*1000:.3f}ms py={py_p50*1000:.3f}ms"
    assert cpp_p75 < py_p75, f"p75: cpp={cpp_p75*1000:.3f}ms py={py_p75*1000:.3f}ms"
    assert cpp_p95 < py_p95, f"p95: cpp={cpp_p95*1000:.3f}ms py={py_p95*1000:.3f}ms"


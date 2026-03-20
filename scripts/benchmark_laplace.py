from __future__ import annotations

import argparse
import random
import statistics
import time

from flow_core.quant.laplace_zhu import price_laplace_zhu_call, price_laplace_zhu_put


def _sample_params(n: int, seed: int) -> list[tuple[float, float, float, float, float, float, bool]]:
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
    return statistics.quantiles(vals, n=100)[p - 1]


def main(runs: int, seed: int) -> None:
    import quantcore  # type: ignore

    params = _sample_params(runs, seed)
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

    print(f"runs={runs}")
    print(f"python  p50={py_p50*1000:.3f}ms p75={py_p75*1000:.3f}ms p95={py_p95*1000:.3f}ms")
    print(f"cpp     p50={cpp_p50*1000:.3f}ms p75={cpp_p75*1000:.3f}ms p95={cpp_p95*1000:.3f}ms")
    print(f"speedup p50={py_p50/cpp_p50:.2f}x p75={py_p75/cpp_p75:.2f}x p95={py_p95/cpp_p95:.2f}x")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark Python vs C++ Laplace (p50/p75/p95).")
    parser.add_argument("--runs", type=int, default=400)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    main(runs=args.runs, seed=args.seed)


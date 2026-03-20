from __future__ import annotations

import statistics
import time

import numpy as np
import polars as pl

from flow_core.quant.ssvi import calibrate_ssvi


def synthetic_chain(rows: int = 120) -> pl.DataFrame:
    strikes = np.linspace(350.0, 550.0, rows)
    iv = 0.15 + 0.20 * ((strikes - 450.0) / 100.0) ** 2
    return pl.DataFrame(
        {
            "strike": strikes,
            "implied_vol_vendor": iv,
            "underlying_price": np.full(rows, 450.0),
        }
    )


def main(runs: int = 50) -> None:
    frame = synthetic_chain()
    timings = []
    for _ in range(runs):
        start = time.perf_counter()
        calibrate_ssvi(frame, cold_start_multistart=True)
        timings.append(time.perf_counter() - start)

    p95 = statistics.quantiles(timings, n=20)[18]
    print(f"p95={p95*1000:.2f} ms over {runs} runs")


if __name__ == "__main__":
    main()

from __future__ import annotations

import statistics
import time

import numpy as np
import polars as pl
import pytest

from flow_core.quant.ssvi import calibrate_ssvi


@pytest.mark.perf
def test_ssvi_p95_under_250ms() -> None:
    strikes = np.linspace(350.0, 550.0, 100)
    vols = 0.16 + 0.18 * ((strikes - 450.0) / 100.0) ** 2
    frame = pl.DataFrame(
        {
            "strike": strikes,
            "implied_vol_vendor": vols,
            "underlying_price": np.full(strikes.size, 450.0),
        }
    )

    timings = []
    for _ in range(30):
        start = time.perf_counter()
        calibrate_ssvi(frame, cold_start_multistart=False)
        timings.append(time.perf_counter() - start)

    p95 = statistics.quantiles(timings, n=20)[18]
    assert p95 < 0.25

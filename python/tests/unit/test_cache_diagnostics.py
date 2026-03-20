from __future__ import annotations

import polars as pl

from flow_core.orchestration.cache import InMemoryQuoteCache


async def test_cache_stores_parity_and_dispatch() -> None:
    cache = InMemoryQuoteCache()
    parity = pl.DataFrame({"winner_model": ["luba"]})
    parity_detail = pl.DataFrame({"model": ["luba"], "parity_error": [0.01]})
    dispatch = pl.DataFrame({"iv_engine": ["bjerksund_stensland"]})
    ssvi = pl.DataFrame({"fit_space": ["log"], "objective": [0.01]})
    greeks = pl.DataFrame({"greeks_engine": ["binomial_richardson"], "delta": [0.5]})
    calibration = pl.DataFrame(
        {
            "symbol": ["SPY"],
            "asof_ts": [None],
            "expiration": [None],
            "model_id": ["ssvi_primary_log"],
            "converged": [True],
            "iterations": [10],
            "sse_final": [0.001],
            "durrleman_pass": [True],
            "params": [{"a": 0.01, "b": 0.1, "rho": -0.2, "m": 0.0, "sigma": 0.25}],
        }
    )

    await cache.upsert_parity("SPY", parity)
    await cache.upsert_parity_detail("SPY", parity_detail)
    await cache.upsert_dispatch("SPY", dispatch)
    await cache.upsert_ssvi("SPY", ssvi)
    await cache.upsert_greeks("SPY", greeks)
    await cache.append_calibration_diagnostics("SPY", calibration)

    assert cache.get_parity_nowait("SPY").height == 1
    assert cache.get_parity_detail_nowait("SPY").height == 1
    assert cache.get_dispatch_nowait("SPY").height == 1
    assert cache.get_ssvi_nowait("SPY").height == 1
    assert cache.get_greeks_nowait("SPY").height == 1
    assert cache.get_calibration_diagnostics_nowait("SPY").height == 1
    popped = await cache.pop_calibration_diagnostics("SPY")
    assert popped.height == 1
    assert cache.get_calibration_diagnostics_nowait("SPY").is_empty()

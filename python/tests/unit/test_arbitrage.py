from __future__ import annotations

import polars as pl

from flow_core.quant.arbitrage import scan_arbitrage_violations


def test_arbitrage_scan_no_violations() -> None:
    frame = pl.DataFrame(
        {
            "expiration": ["2026-03-20"] * 6,
            "option_type": ["call", "call", "call", "put", "put", "put"],
            "strike": [95.0, 100.0, 105.0, 95.0, 100.0, 105.0],
            "bid": [7.0, 4.8, 2.9, 1.0, 2.8, 5.0],
            "ask": [7.2, 5.0, 3.1, 1.2, 3.0, 5.2],
        }
    )
    out = scan_arbitrage_violations(frame)
    assert out.is_empty()


def test_arbitrage_scan_vertical_and_butterfly_violations() -> None:
    frame = pl.DataFrame(
        {
            "expiration": ["2026-03-20"] * 6,
            "option_type": ["call", "call", "call", "put", "put", "put"],
            "strike": [95.0, 100.0, 105.0, 95.0, 100.0, 105.0],
            "bid": [10.0, 11.0, 5.0, 3.0, 2.0, 1.0],
            "ask": [10.2, 11.2, 5.2, 3.2, 2.2, 1.2],
        }
    )
    out = scan_arbitrage_violations(frame, tol=1e-6)
    assert not out.is_empty()
    types = set(out["violation_type"].to_list())
    assert "vertical_monotonicity" in types
    assert "butterfly_convexity" in types

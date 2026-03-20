from __future__ import annotations

from datetime import date

import polars as pl

from flow_core.quant.dispatch import build_dispatch_summary


def test_build_dispatch_summary_groups_by_engine_and_expiry() -> None:
    frame = pl.DataFrame(
        {
            "expiration": [date(2026, 3, 20), date(2026, 3, 20), date(2026, 3, 27)],
            "iv_engine": ["bjerksund_stensland", "bjerksund_stensland", "luba_2point"],
            "greeks_engine": ["binomial_richardson", "binomial_richardson", "rim"],
            "implied_vol_vendor": [0.21, 0.19, 0.25],
        }
    )
    out = build_dispatch_summary(frame)
    assert out.height == 2
    assert set(out["iv_engine"].to_list()) == {"bjerksund_stensland", "luba_2point"}
    assert out["contracts"].sum() == 3

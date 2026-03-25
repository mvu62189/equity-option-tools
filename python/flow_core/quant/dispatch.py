from __future__ import annotations

import polars as pl


def build_dispatch_summary(frame: pl.DataFrame) -> pl.DataFrame:
    vol_col = "implied_vol_input" if "implied_vol_input" in frame.columns else ("iv_ref" if "iv_ref" in frame.columns else "implied_vol_vendor")
    needed = {"expiration", "iv_engine", "greeks_engine", vol_col}
    if frame.is_empty() or not needed.issubset(frame.columns):
        return pl.DataFrame(
            {
                "expiration": [],
                "iv_engine": [],
                "greeks_engine": [],
                "contracts": [],
                "avg_iv": [],
                "min_iv": [],
                "max_iv": [],
            }
        )

    filtered = frame.filter(
        pl.col(vol_col).is_not_null()
        & (pl.col(vol_col) > 0.0)
        & pl.col(vol_col).is_finite()
    )
    if filtered.is_empty():
        return pl.DataFrame(
            {
                "expiration": [],
                "iv_engine": [],
                "greeks_engine": [],
                "contracts": [],
                "avg_iv": [],
                "min_iv": [],
                "max_iv": [],
            }
        )

    return (
        filtered.group_by(["expiration", "iv_engine", "greeks_engine"])
        .agg(
            pl.len().alias("contracts"),
            pl.mean(vol_col).alias("avg_iv"),
            pl.min(vol_col).alias("min_iv"),
            pl.max(vol_col).alias("max_iv"),
        )
        .sort(["expiration", "iv_engine", "greeks_engine"])
    )

from __future__ import annotations

import polars as pl


def build_dispatch_summary(frame: pl.DataFrame) -> pl.DataFrame:
    needed = {"expiration", "iv_engine", "greeks_engine", "implied_vol_vendor"}
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
        pl.col("implied_vol_vendor").is_not_null()
        & (pl.col("implied_vol_vendor") > 0.0)
        & (pl.col("implied_vol_vendor") < 5.0)
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
            pl.mean("implied_vol_vendor").alias("avg_iv"),
            pl.min("implied_vol_vendor").alias("min_iv"),
            pl.max("implied_vol_vendor").alias("max_iv"),
        )
        .sort(["expiration", "iv_engine", "greeks_engine"])
    )

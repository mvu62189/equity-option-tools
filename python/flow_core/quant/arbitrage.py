from __future__ import annotations

from typing import Any

import polars as pl


VIOLATION_COLUMNS = [
    "violation_type",
    "expiration",
    "option_type",
    "strike",
    "metric",
    "threshold",
    "detail",
]


def _empty_violations() -> pl.DataFrame:
    return pl.DataFrame(schema={c: pl.String for c in VIOLATION_COLUMNS})


def scan_arbitrage_violations(frame: pl.DataFrame, tol: float = 1e-4) -> pl.DataFrame:
    """Detect static vertical and butterfly convexity violations from quote mids."""
    needed = {"expiration", "option_type", "strike", "bid", "ask"}
    if not needed.issubset(frame.columns):
        return _empty_violations()

    prepared = (
        frame.select(["expiration", "option_type", "strike", "bid", "ask"])
        .with_columns(
            pl.col("option_type").str.to_lowercase().alias("option_type"),
            ((pl.col("bid") + pl.col("ask")) / 2.0).alias("mid"),
        )
        .filter(
            (pl.col("strike") > 0)
            & (pl.col("bid") >= 0)
            & (pl.col("ask") > 0)
            & pl.col("mid").is_finite()
        )
        .group_by(["expiration", "option_type", "strike"])
        .agg(pl.mean("mid").alias("mid"))
        .sort(["expiration", "option_type", "strike"])
    )

    if prepared.is_empty():
        return _empty_violations()

    rows: list[dict[str, Any]] = []
    groups = prepared.partition_by(["expiration", "option_type"], as_dict=True)
    for group_key, sub in groups.items():
        expiration, option_type = group_key
        strikes = sub["strike"].to_list()
        mids = sub["mid"].to_list()
        if len(strikes) < 2:
            continue

        for i in range(1, len(strikes)):
            step = float(mids[i]) - float(mids[i - 1])
            strike = float(strikes[i])
            if option_type == "call" and step > tol:
                rows.append(
                    {
                        "violation_type": "vertical_monotonicity",
                        "expiration": str(expiration),
                        "option_type": "call",
                        "strike": f"{strike:.4f}",
                        "metric": f"{step:.6f}",
                        "threshold": f"<= {tol:.6f}",
                        "detail": "call mid increased with strike",
                    }
                )
            if option_type == "put" and step < -tol:
                rows.append(
                    {
                        "violation_type": "vertical_monotonicity",
                        "expiration": str(expiration),
                        "option_type": "put",
                        "strike": f"{strike:.4f}",
                        "metric": f"{step:.6f}",
                        "threshold": f">= {-tol:.6f}",
                        "detail": "put mid decreased with strike",
                    }
                )

        if len(strikes) < 3:
            continue

        for i in range(1, len(strikes) - 1):
            k0, k1, k2 = float(strikes[i - 1]), float(strikes[i]), float(strikes[i + 1])
            p0, p1, p2 = float(mids[i - 1]), float(mids[i]), float(mids[i + 1])
            if not (k0 < k1 < k2):
                continue

            left_slope = (p1 - p0) / (k1 - k0)
            right_slope = (p2 - p1) / (k2 - k1)
            slope_gap = left_slope - right_slope

            if slope_gap > tol:
                rows.append(
                    {
                        "violation_type": "butterfly_convexity",
                        "expiration": str(expiration),
                        "option_type": str(option_type),
                        "strike": f"{k1:.4f}",
                        "metric": f"{slope_gap:.6f}",
                        "threshold": f"<= {tol:.6f}",
                        "detail": "slope decreased across adjacent strikes",
                    }
                )

    if not rows:
        return _empty_violations()

    return pl.DataFrame(rows).select(VIOLATION_COLUMNS)

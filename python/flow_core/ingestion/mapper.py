from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import polars as pl

from flow_core.config.models import ProviderMap
from flow_core.contracts.schema import CANONICAL_COLUMNS
from flow_core.contracts.validation import validate_canonical_frame


class MappingError(ValueError):
    pass


def map_provider_records(
    records: pl.DataFrame,
    mapping: ProviderMap,
    snapshot_id: str | None = None,
    underlying_symbol: str | None = None,
) -> pl.DataFrame:
    """Map provider-native frame into canonical schema.

    Drops rows only where bid == 0 and ask == 0.
    """
    if records.is_empty():
        return pl.DataFrame({col: [] for col in CANONICAL_COLUMNS})

    missing = [field for field in mapping.required_fields if field not in records.columns]
    if missing:
        raise MappingError(f"Missing provider fields: {missing}")

    selected = records.select([pl.col(src).alias(dst) for src, dst in mapping.field_map.items()])
    if underlying_symbol:
        selected = selected.with_columns(pl.lit(underlying_symbol).alias("symbol"))

    sid = snapshot_id or str(uuid4())
    if "provider" not in selected.columns:
        selected = selected.with_columns(pl.lit(mapping.provider).alias("provider"))
    if "snapshot_id" not in selected.columns:
        selected = selected.with_columns(pl.lit(sid).alias("snapshot_id"))

    if "asof_ts" in selected.columns:
        selected = selected.with_columns(
            pl.col("asof_ts")
            .cast(pl.Datetime(time_unit="us", time_zone="UTC"), strict=False)
            .fill_null(pl.lit(datetime.now(timezone.utc)))
        )

    if "expiration" in selected.columns:
        selected = selected.with_columns(pl.col("expiration").str.to_date("%Y-%m-%d", strict=False))

    if "option_type" in selected.columns:
        selected = selected.with_columns(pl.col("option_type").str.to_lowercase())

    mapped = selected.with_columns(
        [
            pl.col("bid").cast(pl.Float64, strict=False).fill_nan(0.0).fill_null(0.0).alias("bid"),
            pl.col("ask").cast(pl.Float64, strict=False).fill_nan(0.0).fill_null(0.0).alias("ask"),
            pl.col("volume").cast(pl.Int64, strict=False).fill_null(0).alias("volume"),
            pl.col("open_interest").cast(pl.Int64, strict=False).fill_null(0).alias("open_interest"),
            pl.col("strike").cast(pl.Float64, strict=False).alias("strike"),
            pl.col("underlying_price").cast(pl.Float64, strict=False).alias("underlying_price"),
        ]
    ).with_columns(
        pl.coalesce(
            [
                pl.col("last").cast(pl.Float64, strict=False).fill_nan(None),
                ((pl.col("bid") + pl.col("ask")) / 2.0).cast(pl.Float64),
                pl.lit(0.0),
            ]
        ).alias("last")
    )

    mapped = mapped.filter(~((pl.col("bid") == 0.0) & (pl.col("ask") == 0.0)))

    for col in CANONICAL_COLUMNS:
        if col not in mapped.columns:
            mapped = mapped.with_columns(pl.lit(None).alias(col))

    ordered = mapped.select(CANONICAL_COLUMNS)
    return validate_canonical_frame(ordered)

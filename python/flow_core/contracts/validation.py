from __future__ import annotations

import polars as pl

from .schema import CANONICAL_DTYPES, REQUIRED_CANONICAL_COLUMNS


class SchemaValidationError(ValueError):
    pass


def validate_canonical_frame(frame: pl.DataFrame) -> pl.DataFrame:
    missing = [col for col in REQUIRED_CANONICAL_COLUMNS if col not in frame.columns]
    if missing:
        raise SchemaValidationError(f"Missing required columns: {missing}")

    casts = []
    for name, dtype in CANONICAL_DTYPES.items():
        if name in frame.columns:
            casts.append(pl.col(name).cast(dtype, strict=False))
    casted = frame.with_columns(casts)

    for col in REQUIRED_CANONICAL_COLUMNS:
        if casted[col].null_count() > 0:
            raise SchemaValidationError(f"Column '{col}' has nulls after type coercion")

    return casted

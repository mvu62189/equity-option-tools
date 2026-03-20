from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl

from flow_core.orchestration.calibration_diagnostics import (
    CALIBRATION_DIAGNOSTICS_SCHEMA,
    empty_calibration_diagnostics,
    ensure_calibration_diagnostics_schema,
    make_ssvi_diagnostic_row,
)
from flow_core.quant.models import SSVIResult


def test_calibration_diagnostics_schema_is_strict() -> None:
    empty = empty_calibration_diagnostics()
    assert empty.schema == CALIBRATION_DIAGNOSTICS_SCHEMA

    row = make_ssvi_diagnostic_row(
        symbol="SPY",
        asof_ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        expiration=date(2026, 1, 31),
        model_id="ssvi_primary_log",
        result=SSVIResult(
            a=0.01,
            b=0.10,
            rho=-0.2,
            m=0.0,
            sigma=0.2,
            objective=1e-4,
            success=True,
            iterations=42,
            durrleman_pass=True,
        ),
    )
    assert row.schema == CALIBRATION_DIAGNOSTICS_SCHEMA
    assert row["iterations"].dtype == pl.UInt32


def test_ensure_schema_casts_and_orders_columns() -> None:
    frame = pl.DataFrame(
        {
            "model_id": ["ssvi_primary_log"],
            "symbol": ["SPY"],
            "iterations": [12],
            "converged": [True],
            "sse_final": [0.001],
            "durrleman_pass": [True],
            "asof_ts": ["2026-01-01T00:00:00Z"],
            "expiration": ["2026-01-31T16:00:00Z"],
            "params": [{"a": 0.01, "b": 0.1, "rho": -0.2, "m": 0.0, "sigma": 0.25}],
        }
    )
    out = ensure_calibration_diagnostics_schema(frame)
    assert out.schema == CALIBRATION_DIAGNOSTICS_SCHEMA

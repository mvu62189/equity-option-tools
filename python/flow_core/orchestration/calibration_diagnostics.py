from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import polars as pl

from flow_core.quant.models import SSVIResult

PARAM_KEYS = [
    "a",
    "b",
    "rho",
    "m",
    "sigma",
    "gamma",
    "boundary_t",
    "boundary_inf",
    "quad_n",
    "nodes",
    "count",
    "converged_ratio",
]

PARAMS_DTYPE = pl.Struct(
    [
        pl.Field("a", pl.Float64),
        pl.Field("b", pl.Float64),
        pl.Field("rho", pl.Float64),
        pl.Field("m", pl.Float64),
        pl.Field("sigma", pl.Float64),
        pl.Field("gamma", pl.Float64),
        pl.Field("boundary_t", pl.Float64),
        pl.Field("boundary_inf", pl.Float64),
        pl.Field("quad_n", pl.Float64),
        pl.Field("nodes", pl.Float64),
        pl.Field("count", pl.Float64),
        pl.Field("converged_ratio", pl.Float64),
    ]
)

CALIBRATION_DIAGNOSTICS_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.String,
    "asof_ts": pl.Datetime(time_zone="UTC"),
    "expiration": pl.Datetime(time_zone="UTC"),
    "batch_id": pl.String,
    "snapshot_kind": pl.String,
    "source_mode": pl.String,
    "trading_date": pl.String,
    "model_id": pl.String,
    "backend_used": pl.String,
    "runtime_mode": pl.String,
    "converged": pl.Boolean,
    "iterations": pl.UInt32,
    "sse_final": pl.Float64,
    "durrleman_pass": pl.Boolean,
    "failure_reason": pl.String,
    "jump_interp_mode": pl.String,
    "params": PARAMS_DTYPE,
}

CALIBRATION_DIAGNOSTICS_COLUMNS = list(CALIBRATION_DIAGNOSTICS_SCHEMA.keys())


def _to_utc_dt(value: datetime | date | None) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, 16, 0, 0, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _normalize_params(params: dict[str, Any] | None) -> dict[str, float | None]:
    out: dict[str, float | None] = {k: None for k in PARAM_KEYS}
    if not isinstance(params, dict):
        return out
    for key in PARAM_KEYS:
        value = params.get(key)
        if value is None:
            continue
        try:
            out[key] = float(value)
        except Exception:
            out[key] = None
    return out


def empty_calibration_diagnostics() -> pl.DataFrame:
    return pl.DataFrame(schema=CALIBRATION_DIAGNOSTICS_SCHEMA)


def ensure_calibration_diagnostics_schema(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return empty_calibration_diagnostics()
    out = frame
    if "params" not in out.columns:
        out = out.with_columns(pl.lit(None, dtype=PARAMS_DTYPE).alias("params"))
    else:
        normalized_params = [_normalize_params(x if isinstance(x, dict) else None) for x in out["params"].to_list()]
        out = out.with_columns(pl.Series(name="params", values=normalized_params, dtype=PARAMS_DTYPE))

    for col, dtype in CALIBRATION_DIAGNOSTICS_SCHEMA.items():
        if col == "params":
            continue
        if col not in out.columns:
            out = out.with_columns(pl.lit(None, dtype=dtype).alias(col))
        else:
            out = out.with_columns(pl.col(col).cast(dtype, strict=False))
    return out.select(CALIBRATION_DIAGNOSTICS_COLUMNS)


def make_calibration_diagnostic_row(
    *,
    symbol: str,
    asof_ts: datetime | date | None,
    expiration: datetime | date | None,
    model_id: str,
    converged: bool,
    iterations: int,
    sse_final: float,
    durrleman_pass: bool,
    batch_id: str = "",
    snapshot_kind: str = "",
    source_mode: str = "",
    trading_date: str = "",
    backend_used: str = "",
    runtime_mode: str = "",
    failure_reason: str = "",
    jump_interp_mode: str = "",
    params: dict[str, Any] | None = None,
) -> pl.DataFrame:
    row: dict[str, Any] = {
        "symbol": symbol,
        "asof_ts": _to_utc_dt(asof_ts),
        "expiration": _to_utc_dt(expiration),
        "batch_id": batch_id,
        "snapshot_kind": snapshot_kind,
        "source_mode": source_mode,
        "trading_date": trading_date,
        "model_id": model_id,
        "backend_used": backend_used,
        "runtime_mode": runtime_mode,
        "converged": bool(converged),
        "iterations": int(max(iterations, 0)),
        "sse_final": float(sse_final),
        "durrleman_pass": bool(durrleman_pass),
        "failure_reason": failure_reason,
        "jump_interp_mode": jump_interp_mode,
        "params": _normalize_params(params),
    }
    return ensure_calibration_diagnostics_schema(pl.DataFrame([row]))


def make_ssvi_diagnostic_row(
    *,
    symbol: str,
    asof_ts: datetime | date | None,
    expiration: datetime | date | None,
    model_id: str,
    result: SSVIResult,
    batch_id: str = "",
    snapshot_kind: str = "",
    source_mode: str = "",
    trading_date: str = "",
    backend_used: str = "",
    runtime_mode: str = "",
    failure_reason: str = "",
) -> pl.DataFrame:
    return make_calibration_diagnostic_row(
        symbol=symbol,
        asof_ts=asof_ts,
        expiration=expiration,
        model_id=model_id,
        batch_id=batch_id,
        snapshot_kind=snapshot_kind,
        source_mode=source_mode,
        trading_date=trading_date,
        backend_used=backend_used,
        runtime_mode=runtime_mode,
        converged=result.success,
        iterations=result.iterations,
        sse_final=result.objective,
        durrleman_pass=result.durrleman_pass,
        failure_reason=failure_reason,
        params={
            "a": float(result.a),
            "b": float(result.b),
            "rho": float(result.rho),
            "m": float(result.m),
            "sigma": float(result.sigma),
        },
    )

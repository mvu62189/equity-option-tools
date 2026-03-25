from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import polars as pl

RUNTIME_METRICS_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.String,
    "asof_ts": pl.Datetime(time_zone="UTC"),
    "batch_id": pl.String,
    "version": pl.Int64,
    "snapshot_kind": pl.String,
    "source_mode": pl.String,
    "trading_date": pl.String,
    "runtime_mode": pl.String,
    "ingestion_ms": pl.Float64,
    "mapping_ms": pl.Float64,
    "routing_ms": pl.Float64,
    "calibration_ms": pl.Float64,
    "pricing_ms": pl.Float64,
    "ui_bridge_ms": pl.Float64,
    "persist_ms": pl.Float64,
    "total_ms": pl.Float64,
    "overlay_prep_ms": pl.Float64,
    "hydrate_ms": pl.Float64,
    "raw_rows": pl.Int64,
    "greeks_rows": pl.Int64,
    "surface_rows": pl.Int64,
    "surface_summary_rows": pl.Int64,
    "diagnostics_rows": pl.Int64,
    "state_bytes_total": pl.Int64,
    "state_bytes_raw": pl.Int64,
    "state_bytes_greeks": pl.Int64,
    "drop_raw": pl.Int64,
    "drop_greeks": pl.Int64,
    "drop_overlay": pl.Int64,
    "drop_surface_points": pl.Int64,
}


def empty_runtime_metrics() -> pl.DataFrame:
    return pl.DataFrame(schema=RUNTIME_METRICS_SCHEMA)


def make_runtime_metrics_row(
    *,
    symbol: str,
    asof_ts: datetime | None,
    batch_id: str,
    version: int,
    snapshot_kind: str,
    source_mode: str,
    trading_date: str,
    runtime_mode: str,
    latency_ms: dict[str, float],
    raw_rows: int,
    greeks_rows: int,
    surface_rows: int,
    surface_summary_rows: int,
    diagnostics_rows: int,
    memory_bytes: dict[str, int] | None = None,
    drop_counters: dict[str, int] | None = None,
    overlay_prep_ms: float = 0.0,
    hydrate_ms: float = 0.0,
) -> pl.DataFrame:
    memory = dict(memory_bytes or {})
    drops = dict(drop_counters or {})
    row: dict[str, Any] = {
        "symbol": symbol,
        "asof_ts": asof_ts if asof_ts is not None else datetime.now(timezone.utc),
        "batch_id": batch_id,
        "version": int(version),
        "snapshot_kind": snapshot_kind,
        "source_mode": source_mode,
        "trading_date": trading_date,
        "runtime_mode": runtime_mode,
        "ingestion_ms": float(latency_ms.get("ingestion_ms", 0.0)),
        "mapping_ms": float(latency_ms.get("mapping_ms", 0.0)),
        "routing_ms": float(latency_ms.get("routing_ms", 0.0)),
        "calibration_ms": float(latency_ms.get("calibration_ms", 0.0)),
        "pricing_ms": float(latency_ms.get("pricing_ms", 0.0)),
        "ui_bridge_ms": float(latency_ms.get("ui_bridge_ms", 0.0)),
        "persist_ms": float(latency_ms.get("persist_ms", 0.0)),
        "total_ms": float(latency_ms.get("total_ms", 0.0)),
        "overlay_prep_ms": float(overlay_prep_ms),
        "hydrate_ms": float(hydrate_ms),
        "raw_rows": int(raw_rows),
        "greeks_rows": int(greeks_rows),
        "surface_rows": int(surface_rows),
        "surface_summary_rows": int(surface_summary_rows),
        "diagnostics_rows": int(diagnostics_rows),
        "state_bytes_total": int(memory.get("total", 0)),
        "state_bytes_raw": int(memory.get("raw", 0)),
        "state_bytes_greeks": int(memory.get("greeks", 0)),
        "drop_raw": int(drops.get("raw", 0)),
        "drop_greeks": int(drops.get("greeks", 0)),
        "drop_overlay": int(drops.get("overlay", 0)),
        "drop_surface_points": int(drops.get("surface_points", 0)),
    }
    return pl.DataFrame([row], schema=RUNTIME_METRICS_SCHEMA)

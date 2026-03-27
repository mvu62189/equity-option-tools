from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import numpy as np
import polars as pl


DATASET_NAMES = [
    "raw",
    "greeks",
    "model_greeks",
    "ssvi",
    "dispatch",
    "parity",
    "parity_detail",
    "diagnostics",
    "quote_quality_points",
    "surface_points",
    "surface_diagnostics",
    "focus_expiry_summary",
    "dealer_exposure_points",
    "flow_proxy_points",
    "scanner_levels",
    "runtime_metrics",
    "overlay",
]

DEFAULT_BUDGET_MB = {
    "raw": 128,
    "greeks": 256,
    "model_greeks": 256,
    "ssvi": 32,
    "dispatch": 16,
    "parity": 64,
    "parity_detail": 128,
    "diagnostics": 64,
    "quote_quality_points": 128,
    "surface_points": 256,
    "surface_diagnostics": 64,
    "focus_expiry_summary": 32,
    "dealer_exposure_points": 96,
    "flow_proxy_points": 96,
    "scanner_levels": 96,
    "runtime_metrics": 32,
    "overlay": 128,
}

DEFAULT_ROW_CAPS = {
    "raw": 10_000,
    "greeks": 10_000,
    "model_greeks": 10_000,
    "ssvi": 2_000,
    "dispatch": 5_000,
    "parity": 5_000,
    "parity_detail": 10_000,
    "diagnostics": 2_000,
    "quote_quality_points": 10_000,
    "surface_points": 10_000,
    "surface_diagnostics": 2_000,
    "focus_expiry_summary": 2_000,
    "dealer_exposure_points": 10_000,
    "flow_proxy_points": 10_000,
    "scanner_levels": 10_000,
    "runtime_metrics": 2_000,
}

TRIM_ORDER = ["parity_detail", "diagnostics", "ssvi", "quote_quality_points", "model_greeks", "greeks", "raw"]


@dataclass(frozen=True, slots=True)
class BatchPayload:
    symbol: str
    batch_id: str
    version_hint: int | None
    updated_at_utc: datetime
    raw: pl.DataFrame
    greeks: pl.DataFrame
    ssvi: pl.DataFrame
    dispatch: pl.DataFrame
    parity: pl.DataFrame
    parity_detail: pl.DataFrame
    calibration_diag: pl.DataFrame
    trading_date: str = ""
    snapshot_kind: str = "live_batch"
    source_mode: str = "live"
    is_final_for_day: bool = False
    parent_batch_id: str = ""
    latency_ms: dict[str, float] = field(default_factory=dict)
    status: dict[str, str | bool | float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SymbolSnapshot:
    symbol: str
    batch_id: str
    version: int
    updated_at_utc: datetime
    raw: pl.DataFrame
    greeks: pl.DataFrame
    ssvi: pl.DataFrame
    dispatch: pl.DataFrame
    parity: pl.DataFrame
    parity_detail: pl.DataFrame
    calibration_diag_tail: pl.DataFrame
    overlay_payloads: dict[str, object]
    memory_bytes: dict[str, int]
    drop_counters: dict[str, int]
    latency_ms: dict[str, float]
    status: dict[str, str | bool | float]
    trading_date: str = ""
    snapshot_kind: str = "live_batch"
    source_mode: str = "live"
    is_final_for_day: bool = False
    parent_batch_id: str = ""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _frame_bytes(frame: pl.DataFrame) -> int:
    if frame.is_empty():
        return 0
    try:
        return int(frame.estimated_size())
    except Exception:
        return 0


def _overlay_bytes(payloads: dict[str, object]) -> int:
    total = 0
    for value in payloads.values():
        if isinstance(value, np.ndarray):
            total += int(value.nbytes)
        elif isinstance(value, dict):
            for nested in value.values():
                if isinstance(nested, np.ndarray):
                    total += int(nested.nbytes)
    return total


def _tail_by_budget(frame: pl.DataFrame, *, max_rows: int, max_bytes: int) -> tuple[pl.DataFrame, int]:
    if frame.is_empty():
        return frame, 0

    before = frame.height
    out = frame
    if max_rows > 0 and out.height > max_rows:
        out = out.tail(max_rows)

    if max_bytes > 0:
        bytes_now = _frame_bytes(out)
        if bytes_now > max_bytes and out.height > 1:
            keep = max(1, int(out.height * (max_bytes / max(bytes_now, 1))))
            keep = min(keep, out.height)
            out = out.tail(keep)

    dropped = max(before - out.height, 0)
    return out, dropped


class LiveStateStore:
    def __init__(
        self,
        *,
        dataset_budgets_mb: dict[str, int] | None = None,
        dataset_row_caps: dict[str, int] | None = None,
        max_symbols: int = 3,
    ) -> None:
        budgets_mb = dict(DEFAULT_BUDGET_MB)
        budgets_mb.update(dataset_budgets_mb or {})
        row_caps = dict(DEFAULT_ROW_CAPS)
        row_caps.update(dataset_row_caps or {})

        self._budgets_bytes = {k: max(int(v), 0) * 1024 * 1024 for k, v in budgets_mb.items()}
        self._row_caps = {k: max(int(v), 0) for k, v in row_caps.items()}
        self._max_symbols = max(max_symbols, 1)

        self._lock = Lock()
        self._snapshots: dict[str, SymbolSnapshot] = {}
        self._versions: dict[str, int] = {}
        self._histories: dict[str, dict[str, pl.DataFrame]] = {}

    def publish(self, payload: BatchPayload) -> int:
        with self._lock:
            latest = self._versions.get(payload.symbol, 0)
            version = payload.version_hint if payload.version_hint and payload.version_hint > latest else latest + 1
            self._versions[payload.symbol] = version

            if payload.symbol not in self._snapshots and len(self._snapshots) >= self._max_symbols:
                oldest_symbol = min(self._snapshots.items(), key=lambda kv: kv[1].updated_at_utc)[0]
                self._snapshots.pop(oldest_symbol, None)
                self._versions.pop(oldest_symbol, None)
                self._histories.pop(oldest_symbol, None)

            prev = self._snapshots.get(payload.symbol)
            drop_counters = dict(prev.drop_counters) if prev is not None else {k: 0 for k in DATASET_NAMES}

            snapshot = SymbolSnapshot(
                symbol=payload.symbol,
                batch_id=payload.batch_id,
                version=version,
                updated_at_utc=payload.updated_at_utc,
                trading_date=payload.trading_date,
                snapshot_kind=payload.snapshot_kind,
                source_mode=payload.source_mode,
                is_final_for_day=payload.is_final_for_day,
                parent_batch_id=payload.parent_batch_id,
                raw=payload.raw,
                greeks=payload.greeks,
                ssvi=payload.ssvi,
                dispatch=payload.dispatch,
                parity=payload.parity,
                parity_detail=payload.parity_detail,
                calibration_diag_tail=payload.calibration_diag,
                overlay_payloads=dict(prev.overlay_payloads) if prev is not None else {},
                memory_bytes={},
                drop_counters=drop_counters,
                latency_ms=dict(payload.latency_ms),
                status=dict(payload.status),
            )
            snapshot = replace(snapshot, memory_bytes=self._estimate_snapshot_bytes(snapshot))
            self._snapshots[payload.symbol] = snapshot
            return version

    def get_snapshot(self, symbol: str) -> SymbolSnapshot | None:
        with self._lock:
            return self._snapshots.get(symbol)

    def get_latest_version(self, symbol: str) -> int:
        with self._lock:
            return self._versions.get(symbol, 0)

    def get_history(self, symbol: str, dataset: str) -> pl.DataFrame:
        with self._lock:
            histories = self._histories.get(symbol, {})
            return histories.get(dataset, pl.DataFrame())

    def get_symbols(self) -> list[str]:
        with self._lock:
            return sorted(self._snapshots.keys())

    def publish_overlay_payloads(self, symbol: str, version: int, overlay_payloads: dict[str, object]) -> bool:
        with self._lock:
            snapshot = self._snapshots.get(symbol)
            if snapshot is None or snapshot.version != version:
                return False
            merged = dict(snapshot.overlay_payloads)
            merged.update(overlay_payloads)
            updated = replace(snapshot, overlay_payloads=merged)
            updated = replace(updated, memory_bytes=self._estimate_snapshot_bytes(updated))
            self._snapshots[symbol] = updated
            return True

    def increment_drop_counter(self, symbol: str, dataset: str, amount: int = 1) -> None:
        if amount <= 0:
            return
        with self._lock:
            snapshot = self._snapshots.get(symbol)
            if snapshot is None:
                return
            counters = dict(snapshot.drop_counters)
            counters[dataset] = counters.get(dataset, 0) + amount
            self._snapshots[symbol] = replace(snapshot, drop_counters=counters)

    def append_history(self, symbol: str, dataset: str, frame: pl.DataFrame) -> None:
        if frame.is_empty():
            return
        with self._lock:
            histories = self._histories.setdefault(symbol, {})
            prev = histories.get(dataset)
            merged = frame if prev is None or prev.is_empty() else prev.vstack(frame, in_place=False)
            cap = self._row_caps.get(dataset, 0)
            budget = self._budgets_bytes.get(dataset, 0)
            trimmed, dropped = _tail_by_budget(merged, max_rows=cap, max_bytes=budget)
            histories[dataset] = trimmed
            if dropped > 0:
                self._increment_drop_counter_locked(symbol, dataset, dropped)

    def trim_to_budget(self, symbol: str) -> dict[str, int]:
        with self._lock:
            snapshot = self._snapshots.get(symbol)
            if snapshot is None:
                return {}

            drops: dict[str, int] = {}
            updated = snapshot
            counters = dict(updated.drop_counters)
            for dataset in TRIM_ORDER:
                frame = self._get_frame_by_dataset(updated, dataset)
                trimmed, dropped = _tail_by_budget(
                    frame,
                    max_rows=self._row_caps.get(dataset, 0),
                    max_bytes=self._budgets_bytes.get(dataset, 0),
                )
                if dropped > 0:
                    drops[dataset] = dropped
                    updated = self._set_frame_by_dataset(updated, dataset, trimmed)
                    counters[dataset] = counters.get(dataset, 0) + dropped

            updated = replace(updated, drop_counters=counters)
            updated = replace(updated, memory_bytes=self._estimate_snapshot_bytes(updated))
            self._snapshots[symbol] = updated
            return drops

    def estimate_symbol_bytes(self, symbol: str) -> dict[str, int]:
        with self._lock:
            snapshot = self._snapshots.get(symbol)
            if snapshot is None:
                return {}
            return dict(snapshot.memory_bytes)

    def estimate_total_bytes(self) -> int:
        with self._lock:
            total = 0
            for snapshot in self._snapshots.values():
                total += int(snapshot.memory_bytes.get("total", 0))
            return total

    def stats_frame(self) -> pl.DataFrame:
        with self._lock:
            rows: list[dict[str, Any]] = []
            for symbol, snap in self._snapshots.items():
                rows.append(
                    {
                        "symbol": symbol,
                        "version": snap.version,
                        "batch_id": snap.batch_id,
                        "snapshot_kind": snap.snapshot_kind,
                        "source_mode": snap.source_mode,
                        "is_final_for_day": snap.is_final_for_day,
                        "updated_at_utc": snap.updated_at_utc,
                        "raw_rows": snap.raw.height,
                        "greeks_rows": snap.greeks.height,
                        "diag_rows": snap.calibration_diag_tail.height,
                        "total_bytes": snap.memory_bytes.get("total", 0),
                        "overlay_bytes": snap.memory_bytes.get("overlay", 0),
                    }
                )
            return pl.DataFrame(rows) if rows else pl.DataFrame(
                schema={
                    "symbol": pl.String,
                    "version": pl.Int64,
                    "batch_id": pl.String,
                    "snapshot_kind": pl.String,
                    "source_mode": pl.String,
                    "is_final_for_day": pl.Boolean,
                    "updated_at_utc": pl.Datetime(time_zone="UTC"),
                    "raw_rows": pl.Int64,
                    "greeks_rows": pl.Int64,
                    "diag_rows": pl.Int64,
                    "total_bytes": pl.Int64,
                    "overlay_bytes": pl.Int64,
                }
            )

    def _estimate_snapshot_bytes(self, snapshot: SymbolSnapshot) -> dict[str, int]:
        raw = _frame_bytes(snapshot.raw)
        greeks = _frame_bytes(snapshot.greeks)
        ssvi = _frame_bytes(snapshot.ssvi)
        dispatch = _frame_bytes(snapshot.dispatch)
        parity = _frame_bytes(snapshot.parity)
        parity_detail = _frame_bytes(snapshot.parity_detail)
        diagnostics = _frame_bytes(snapshot.calibration_diag_tail)
        overlay = _overlay_bytes(snapshot.overlay_payloads)
        total = raw + greeks + ssvi + dispatch + parity + parity_detail + diagnostics + overlay
        return {
            "raw": raw,
            "greeks": greeks,
            "ssvi": ssvi,
            "dispatch": dispatch,
            "parity": parity,
            "parity_detail": parity_detail,
            "diagnostics": diagnostics,
            "overlay": overlay,
            "total": total,
        }

    def _get_frame_by_dataset(self, snapshot: SymbolSnapshot, dataset: str) -> pl.DataFrame:
        if dataset == "raw":
            return snapshot.raw
        if dataset == "greeks":
            return snapshot.greeks
        if dataset == "ssvi":
            return snapshot.ssvi
        if dataset == "dispatch":
            return snapshot.dispatch
        if dataset == "parity":
            return snapshot.parity
        if dataset == "parity_detail":
            return snapshot.parity_detail
        if dataset == "diagnostics":
            return snapshot.calibration_diag_tail
        return pl.DataFrame()

    def _set_frame_by_dataset(self, snapshot: SymbolSnapshot, dataset: str, frame: pl.DataFrame) -> SymbolSnapshot:
        if dataset == "raw":
            return replace(snapshot, raw=frame)
        if dataset == "greeks":
            return replace(snapshot, greeks=frame)
        if dataset == "ssvi":
            return replace(snapshot, ssvi=frame)
        if dataset == "dispatch":
            return replace(snapshot, dispatch=frame)
        if dataset == "parity":
            return replace(snapshot, parity=frame)
        if dataset == "parity_detail":
            return replace(snapshot, parity_detail=frame)
        if dataset == "diagnostics":
            return replace(snapshot, calibration_diag_tail=frame)
        return snapshot

    def _increment_drop_counter_locked(self, symbol: str, dataset: str, amount: int) -> None:
        snapshot = self._snapshots.get(symbol)
        if snapshot is None or amount <= 0:
            return
        counters = dict(snapshot.drop_counters)
        counters[dataset] = counters.get(dataset, 0) + amount
        self._snapshots[symbol] = replace(snapshot, drop_counters=counters)


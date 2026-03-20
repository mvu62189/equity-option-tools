from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from flow_core.orchestration.state_store import BatchPayload


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _empty() -> pl.DataFrame:
    return pl.DataFrame()


def _read_hive_dataset(root: Path) -> pl.DataFrame:
    if not root.exists():
        return pl.DataFrame()
    try:
        return pl.read_parquet(str(root / "**" / "*.parquet"), hive_partitioning=True)
    except Exception:
        return pl.DataFrame()


def _coerce_ts(value) -> datetime:  # noqa: ANN001
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return _utc_now()


def _with_schema_defaults(frame: pl.DataFrame, **defaults) -> pl.DataFrame:
    out = frame
    for key, value in defaults.items():
        if key not in out.columns:
            out = out.with_columns(pl.lit(value).alias(key))
    return out


@dataclass(slots=True)
class SnapshotBootstrapLoader:
    raw_root: str
    derived_root: str

    def load_latest_symbol_payload(self, symbol: str) -> BatchPayload | None:
        catalog = self._read_dataset("snapshot_catalog").filter(pl.col("symbol") == symbol) if self._has_dataset("snapshot_catalog") else _empty()
        row = None
        if not catalog.is_empty():
            if "is_final_for_day" in catalog.columns and bool(catalog["is_final_for_day"].cast(pl.Boolean, strict=False).sum()) > 0:
                catalog = catalog.filter(pl.col("is_final_for_day") == True)  # noqa: E712
            sort_cols = [c for c in ("asof_ts", "updated_at_utc") if c in catalog.columns]
            if sort_cols:
                catalog = catalog.sort(sort_cols, descending=[True] * len(sort_cols))
            row = catalog.to_dicts()[0]

        if row is None:
            return self._load_legacy_latest(symbol)

        batch_id = str(row.get("batch_id", ""))
        snapshot_kind = str(row.get("snapshot_kind", "offline_bootstrap"))
        source_mode = "offline_bootstrap"
        trading_date = str(row.get("trading_date", ""))
        parent_batch_id = str(row.get("parent_batch_id", "") or "")
        asof_ts = _coerce_ts(row.get("asof_ts") or row.get("updated_at_utc"))
        is_final_for_day = bool(row.get("is_final_for_day", False))

        raw = self._filter_dataset(self._read_raw(), symbol=symbol, batch_id=batch_id)
        greeks = self._filter_dataset(self._read_dataset("greeks"), symbol=symbol, batch_id=batch_id)
        ssvi = self._filter_dataset(self._read_dataset("ssvi"), symbol=symbol, batch_id=batch_id)
        dispatch = self._filter_dataset(self._read_dataset("dispatch"), symbol=symbol, batch_id=batch_id)
        parity = self._filter_dataset(self._read_dataset("parity"), symbol=symbol, batch_id=batch_id)
        parity_detail = self._filter_dataset(self._read_dataset("parity_detail"), symbol=symbol, batch_id=batch_id)
        diagnostics = self._filter_dataset(self._read_dataset("diagnostics"), symbol=symbol, batch_id=batch_id)

        if snapshot_kind == "eod_final":
            raw = self._apply_oi_refresh_overlay(raw=raw, symbol=symbol, parent_batch_id=batch_id, trading_date=trading_date)

        status = {
            "bootstrapped": True,
            "bootstrap_source": "parquet_latest_final",
            "snapshot_kind": snapshot_kind,
        }
        return BatchPayload(
            symbol=symbol,
            batch_id=batch_id or f"{symbol}:offline:{int(asof_ts.timestamp())}",
            version_hint=1,
            updated_at_utc=asof_ts,
            raw=raw,
            greeks=greeks,
            ssvi=ssvi,
            dispatch=dispatch,
            parity=parity,
            parity_detail=parity_detail,
            calibration_diag=diagnostics,
            trading_date=trading_date,
            snapshot_kind="offline_bootstrap",
            source_mode=source_mode,
            is_final_for_day=is_final_for_day,
            parent_batch_id=parent_batch_id or batch_id,
            latency_ms={},
            status=status,
        )

    def _load_legacy_latest(self, symbol: str) -> BatchPayload | None:
        raw = self._read_raw()
        if raw.is_empty() or "symbol" not in raw.columns:
            return None
        raw = raw.filter(pl.col("symbol") == symbol)
        if raw.is_empty():
            return None
        if "asof_ts" in raw.columns:
            latest_ts = raw.select(pl.max("asof_ts").alias("asof_ts"))["asof_ts"][0]
            raw = raw.filter(pl.col("asof_ts") == latest_ts)
            asof_ts = _coerce_ts(latest_ts)
        else:
            asof_ts = _utc_now()

        greeks = self._legacy_latest_dataset("greeks", symbol, asof_ts)
        ssvi = self._legacy_latest_dataset("ssvi", symbol, asof_ts)
        dispatch = self._legacy_latest_dataset("dispatch", symbol, asof_ts)
        parity = self._legacy_latest_dataset("parity", symbol, asof_ts)
        parity_detail = self._legacy_latest_dataset("parity_detail", symbol, asof_ts)
        diagnostics = self._legacy_latest_dataset("diagnostics", symbol, asof_ts)
        return BatchPayload(
            symbol=symbol,
            batch_id=f"{symbol}:legacy_bootstrap:{int(asof_ts.timestamp())}",
            version_hint=1,
            updated_at_utc=asof_ts,
            raw=_with_schema_defaults(raw, batch_id=f"{symbol}:legacy_bootstrap:{int(asof_ts.timestamp())}", snapshot_kind="offline_bootstrap", source_mode="legacy_parquet"),
            greeks=greeks,
            ssvi=ssvi,
            dispatch=dispatch,
            parity=parity,
            parity_detail=parity_detail,
            calibration_diag=diagnostics,
            trading_date=str(asof_ts.date()),
            snapshot_kind="offline_bootstrap",
            source_mode="legacy_parquet",
            is_final_for_day=False,
            parent_batch_id="",
            latency_ms={},
            status={"bootstrapped": True, "bootstrap_source": "legacy_parquet"},
        )

    def load_symbol_dataset_history(self, symbol: str, dataset: str) -> pl.DataFrame:
        frame = self._read_raw() if dataset == "raw" else self._read_dataset(dataset)
        if frame.is_empty():
            return frame
        if "symbol" in frame.columns:
            frame = frame.filter(pl.col("symbol") == symbol)
        sort_cols = [c for c in ("asof_ts", "updated_at_utc", "expiration", "strike") if c in frame.columns]
        if sort_cols:
            descending = [False] * len(sort_cols)
            frame = frame.sort(sort_cols, descending=descending)
        return frame

    def _legacy_latest_dataset(self, dataset: str, symbol: str, asof_ts: datetime) -> pl.DataFrame:
        frame = self._read_dataset(dataset)
        if frame.is_empty() or "symbol" not in frame.columns:
            return frame
        frame = frame.filter(pl.col("symbol") == symbol)
        if frame.is_empty():
            return frame
        if "asof_ts" in frame.columns:
            return frame.filter(pl.col("asof_ts") == asof_ts)
        return frame

    def _apply_oi_refresh_overlay(self, *, raw: pl.DataFrame, symbol: str, parent_batch_id: str, trading_date: str) -> pl.DataFrame:
        refresh = self._read_dataset("oi_refresh_deltas")
        if refresh.is_empty():
            return raw
        required = {"symbol", "parent_batch_id", "expiration", "option_type", "strike"}
        if not required.issubset(refresh.columns):
            return raw
        refresh = refresh.filter((pl.col("symbol") == symbol) & (pl.col("parent_batch_id") == parent_batch_id))
        if trading_date and "trading_date" in refresh.columns:
            refresh = refresh.filter(pl.col("trading_date").cast(pl.String) == trading_date)
        if refresh.is_empty():
            return raw
        join_keys = ["symbol", "expiration", "option_type", "strike"]
        delta_cols = [c for c in ("open_interest", "volume") if c in refresh.columns]
        if not delta_cols:
            return raw
        suffixed = refresh.select(join_keys + delta_cols).rename({c: f"{c}__oi_refresh" for c in delta_cols})
        merged = raw.join(suffixed, on=join_keys, how="left")
        for col in delta_cols:
            merged = merged.with_columns(
                pl.coalesce(pl.col(f"{col}__oi_refresh"), pl.col(col)).alias(col)
            ).drop(f"{col}__oi_refresh")
        return merged

    def _filter_dataset(self, frame: pl.DataFrame, *, symbol: str, batch_id: str) -> pl.DataFrame:
        if frame.is_empty():
            return frame
        if "symbol" in frame.columns:
            frame = frame.filter(pl.col("symbol") == symbol)
        if batch_id and "batch_id" in frame.columns:
            frame = frame.filter(pl.col("batch_id") == batch_id)
        return frame

    def _has_dataset(self, dataset: str) -> bool:
        return (Path(self.derived_root) / dataset).exists()

    def _read_raw(self) -> pl.DataFrame:
        return _read_hive_dataset(Path(self.raw_root))

    def _read_dataset(self, dataset: str) -> pl.DataFrame:
        return _read_hive_dataset(Path(self.derived_root) / dataset)

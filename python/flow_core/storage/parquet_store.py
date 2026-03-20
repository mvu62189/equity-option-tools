from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import polars as pl


class ParquetStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    @staticmethod
    def _ensure_batch_columns(frame: pl.DataFrame) -> pl.DataFrame:
        out = frame
        if out.is_empty():
            return out
        defaults = {
            "batch_id": "",
            "snapshot_kind": "legacy",
            "source_mode": "legacy",
            "trading_date": "",
            "input_snapshot_kind": "",
            "market_bid": None,
            "market_ask": None,
            "market_last": None,
            "market_mid": None,
            "rate_used": None,
            "dividend_used": None,
            "tau_years": None,
            "model_price": None,
            "display_price": None,
            "display_price_source": "model_price",
        }
        for col, value in defaults.items():
            if col not in out.columns:
                out = out.with_columns(pl.lit(value).alias(col))
        if "trading_date" in out.columns and "asof_ts" in out.columns:
            try:
                out = out.with_columns(
                    pl.when(pl.col("trading_date").cast(pl.String).str.len_chars() > 0)
                    .then(pl.col("trading_date"))
                    .otherwise(pl.col("asof_ts").dt.date().cast(pl.String))
                    .alias("trading_date")
                )
            except Exception:
                pass
        if "input_snapshot_kind" in out.columns and "snapshot_kind" in out.columns:
            out = out.with_columns(
                pl.when(pl.col("input_snapshot_kind").cast(pl.String).str.len_chars() > 0)
                .then(pl.col("input_snapshot_kind"))
                .otherwise(pl.col("snapshot_kind"))
                .alias("input_snapshot_kind")
            )
        if "price" in out.columns:
            if "model_price" in out.columns:
                out = out.with_columns(pl.coalesce(pl.col("model_price"), pl.col("price")).alias("model_price"))
            if "display_price" in out.columns:
                out = out.with_columns(pl.coalesce(pl.col("display_price"), pl.col("price")).alias("display_price"))
        return out

    async def append(self, frame: pl.DataFrame) -> list[Path]:
        return await asyncio.to_thread(self._append_sync, frame)

    async def append_dataset(
        self,
        frame: pl.DataFrame,
        dataset: str,
        partition_col: str = "symbol",
    ) -> list[Path]:
        return await asyncio.to_thread(self._append_dataset_sync, frame, dataset, partition_col)

    def _append_sync(self, frame: pl.DataFrame) -> list[Path]:
        if frame.is_empty():
            return []
        frame = self._ensure_batch_columns(frame)

        paths: list[Path] = []
        for symbol, sub in frame.partition_by("symbol", as_dict=True).items():
            now = datetime.now(timezone.utc)
            ticker = symbol[0] if isinstance(symbol, tuple) else symbol
            base = self.root / f"year={now:%Y}" / f"month={now:%m}" / f"day={now:%d}" / f"ticker={ticker}"
            base.mkdir(parents=True, exist_ok=True)
            out = base / f"{now:%H%M%S_%f}.parquet"
            sub.write_parquet(out)
            paths.append(out)
        return paths

    def _append_dataset_sync(
        self,
        frame: pl.DataFrame,
        dataset: str,
        partition_col: str = "symbol",
    ) -> list[Path]:
        if frame.is_empty():
            return []
        frame = self._ensure_batch_columns(frame)
        if partition_col not in frame.columns:
            frame = frame.with_columns(pl.lit("GLOBAL").alias(partition_col))

        paths: list[Path] = []
        now = datetime.now(timezone.utc)
        for key, sub in frame.partition_by(partition_col, as_dict=True).items():
            part_value = key[0] if isinstance(key, tuple) else key
            base = (
                self.root
                / dataset
                / f"year={now:%Y}"
                / f"month={now:%m}"
                / f"day={now:%d}"
                / f"{partition_col}={part_value}"
            )
            base.mkdir(parents=True, exist_ok=True)
            out = base / f"{now:%H%M%S_%f}.parquet"
            sub.write_parquet(out)
            paths.append(out)
        return paths


class BufferedParquetWriter:
    def __init__(
        self,
        raw_store: ParquetStore,
        derived_store: ParquetStore | None,
        *,
        flush_interval_sec: int = 5,
        flush_max_rows: int = 5_000,
    ) -> None:
        self._raw_store = raw_store
        self._derived_store = derived_store
        self._flush_interval_sec = max(flush_interval_sec, 1)
        self._flush_max_rows = max(flush_max_rows, 100)

        self._raw_frames: list[pl.DataFrame] = []
        self._raw_rows = 0
        self._derived_frames: dict[tuple[str, str], list[pl.DataFrame]] = {}
        self._derived_rows: dict[tuple[str, str], int] = {}

    async def append_raw(self, frame: pl.DataFrame) -> dict[str, Any]:
        if frame.is_empty():
            return {"flush_rows": 0, "flush_files": 0, "flush_ms": 0.0, "buffer_depth": self._raw_rows}
        self._raw_frames.append(frame)
        self._raw_rows += frame.height
        if self._raw_rows >= self._flush_max_rows:
            return await self.flush_raw()
        return {"flush_rows": 0, "flush_files": 0, "flush_ms": 0.0, "buffer_depth": self._raw_rows}

    async def append_derived(
        self,
        frame: pl.DataFrame,
        *,
        dataset: str,
        partition_col: str = "symbol",
    ) -> dict[str, Any]:
        if frame.is_empty() or self._derived_store is None:
            return {"flush_rows": 0, "flush_files": 0, "flush_ms": 0.0, "buffer_depth": 0}
        key = (dataset, partition_col)
        self._derived_frames.setdefault(key, []).append(frame)
        self._derived_rows[key] = self._derived_rows.get(key, 0) + frame.height
        if self._derived_rows[key] >= self._flush_max_rows:
            return await self.flush_derived(dataset=dataset, partition_col=partition_col)
        return {"flush_rows": 0, "flush_files": 0, "flush_ms": 0.0, "buffer_depth": self._derived_rows[key]}

    async def flush_raw(self) -> dict[str, Any]:
        if self._raw_rows <= 0:
            return {"flush_rows": 0, "flush_files": 0, "flush_ms": 0.0, "buffer_depth": 0}
        started = datetime.now(timezone.utc)
        frame = pl.concat(self._raw_frames, how="vertical") if len(self._raw_frames) > 1 else self._raw_frames[0]
        files = await self._raw_store.append(frame)
        elapsed_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
        rows = self._raw_rows
        self._raw_frames = []
        self._raw_rows = 0
        return {"flush_rows": rows, "flush_files": len(files), "flush_ms": elapsed_ms, "buffer_depth": 0}

    async def flush_derived(self, *, dataset: str, partition_col: str = "symbol") -> dict[str, Any]:
        if self._derived_store is None:
            return {"flush_rows": 0, "flush_files": 0, "flush_ms": 0.0, "buffer_depth": 0}
        key = (dataset, partition_col)
        rows = self._derived_rows.get(key, 0)
        frames = self._derived_frames.get(key, [])
        if rows <= 0 or not frames:
            return {"flush_rows": 0, "flush_files": 0, "flush_ms": 0.0, "buffer_depth": 0}
        started = datetime.now(timezone.utc)
        frame = pl.concat(frames, how="vertical") if len(frames) > 1 else frames[0]
        files = await self._derived_store.append_dataset(frame, dataset=dataset, partition_col=partition_col)
        elapsed_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000.0
        self._derived_frames[key] = []
        self._derived_rows[key] = 0
        return {"flush_rows": rows, "flush_files": len(files), "flush_ms": elapsed_ms, "buffer_depth": 0}

    async def flush_all(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {"raw": await self.flush_raw()}
        for dataset, partition_col in list(self._derived_frames.keys()):
            out[f"{dataset}:{partition_col}"] = await self.flush_derived(dataset=dataset, partition_col=partition_col)
        return out

    async def run_periodic_flush(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            await asyncio.sleep(self._flush_interval_sec)
            await self.flush_all()

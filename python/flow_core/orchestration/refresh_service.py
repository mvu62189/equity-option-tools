from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import polars as pl

from flow_core.config.models import PipelineConfig, ProviderMap
from flow_core.ingestion.snapshot import SnapshotIngestor
from flow_core.ingestion.providers.base import ProviderAdapter
from flow_core.orchestration.cache import InMemoryQuoteCache
from flow_core.orchestration.pipeline import QuantPipelineService
from flow_core.orchestration.state_store import BatchPayload, SymbolSnapshot
from flow_core.storage.bootstrap import SnapshotBootstrapLoader


CONTRACT_KEYS = ["symbol", "expiration", "option_type", "strike"]
PRICE_SPACE_COLUMNS = ["bid", "ask", "last", "underlying_price", "implied_vol_vendor", "market_mid"]
NON_PRICE_COLUMNS = ["open_interest", "volume"]


@dataclass(slots=True)
class RefreshDiffResult:
    price_space_changed: bool
    oi_only_changed: bool
    changed_fields: tuple[str, ...]
    changed_contracts: int
    added_contracts: int
    removed_contracts: int


@dataclass(slots=True)
class RefreshResult:
    action: str
    message: str
    snapshot_kind: str = ""
    source_mode: str = ""
    price_space_changed: bool = False
    oi_only_changed: bool = False
    changed_fields: tuple[str, ...] = ()
    changed_contracts: int = 0
    added_contracts: int = 0
    removed_contracts: int = 0


def _market_mid_expr() -> pl.Expr:
    bid = pl.col("bid").cast(pl.Float64, strict=False)
    ask = pl.col("ask").cast(pl.Float64, strict=False)
    last = pl.col("last").cast(pl.Float64, strict=False)
    return (
        pl.when((bid.is_finite() & (bid > 0.0)) | (ask.is_finite() & (ask > 0.0)))
        .then((bid.fill_nan(0.0) + ask.fill_nan(0.0)) * 0.5)
        .otherwise(last)
        .alias("market_mid")
    )


def _normalize_frame(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    out = frame
    for col in ("bid", "ask", "last", "underlying_price", "implied_vol_vendor", "open_interest", "volume", "strike"):
        if col in out.columns:
            out = out.with_columns(pl.col(col).cast(pl.Float64, strict=False).alias(col))
    if "market_mid" not in out.columns:
        if {"bid", "ask", "last"}.intersection(out.columns):
            out = out.with_columns(_market_mid_expr())
        else:
            out = out.with_columns(pl.lit(float("nan")).alias("market_mid"))
    for col in CONTRACT_KEYS:
        if col in out.columns:
            if col == "strike":
                out = out.with_columns(pl.col(col).cast(pl.Float64, strict=False).alias(col))
            else:
                out = out.with_columns(pl.col(col).cast(pl.String).alias(col))
    return out


def _count_changed(joined: pl.DataFrame, col: str, tol: float) -> int:
    lhs = pl.col(col)
    rhs = pl.col(f"{col}__prev")
    expr = (
        (lhs.is_null() & rhs.is_not_null())
        | (lhs.is_not_null() & rhs.is_null())
        | (
            lhs.is_not_null()
            & rhs.is_not_null()
            & ((lhs.cast(pl.Float64, strict=False) - rhs.cast(pl.Float64, strict=False)).abs() > tol)
        )
    )
    return int(joined.select(expr.sum().alias("n"))["n"][0]) if not joined.is_empty() else 0


def compare_refresh_frames(previous: pl.DataFrame, current: pl.DataFrame, *, abs_tol: float = 1e-4) -> RefreshDiffResult:
    prev = _normalize_frame(previous)
    cur = _normalize_frame(current)
    if prev.is_empty() and cur.is_empty():
        return RefreshDiffResult(False, False, (), 0, 0, 0)
    if prev.is_empty() or cur.is_empty():
        size = cur.height if prev.is_empty() else prev.height
        return RefreshDiffResult(True, False, ("structural",), size, cur.height if prev.is_empty() else 0, prev.height if cur.is_empty() else 0)

    if not set(CONTRACT_KEYS).issubset(prev.columns) or not set(CONTRACT_KEYS).issubset(cur.columns):
        return RefreshDiffResult(True, False, ("missing_keys",), max(prev.height, cur.height), 0, 0)

    prev_keys = prev.select(CONTRACT_KEYS).unique()
    cur_keys = cur.select(CONTRACT_KEYS).unique()
    added = cur_keys.join(prev_keys, on=CONTRACT_KEYS, how="anti")
    removed = prev_keys.join(cur_keys, on=CONTRACT_KEYS, how="anti")

    cols = [c for c in PRICE_SPACE_COLUMNS + NON_PRICE_COLUMNS if c in prev.columns and c in cur.columns]
    if not cols:
        return RefreshDiffResult(bool(added.height or removed.height), False, ("structural",) if added.height or removed.height else (), 0, added.height, removed.height)
    joined = cur.select(CONTRACT_KEYS + cols).join(
        prev.select(CONTRACT_KEYS + cols).rename({c: f"{c}__prev" for c in cols}),
        on=CONTRACT_KEYS,
        how="inner",
    )
    changed_fields: list[str] = []
    changed_contracts = 0
    price_changed = added.height > 0 or removed.height > 0
    non_price_changed = False

    for col in PRICE_SPACE_COLUMNS:
        if col not in cols:
            continue
        count = _count_changed(joined, col, abs_tol)
        if count > 0:
            changed_fields.append(col)
            changed_contracts = max(changed_contracts, count)
            price_changed = True

    for col in NON_PRICE_COLUMNS:
        if col not in cols:
            continue
        count = _count_changed(joined, col, abs_tol)
        if count > 0:
            changed_fields.append(col)
            changed_contracts = max(changed_contracts, count)
            non_price_changed = True

    if added.height or removed.height:
        changed_fields.append("contract_set")
        changed_contracts = max(changed_contracts, added.height + removed.height)

    return RefreshDiffResult(
        price_space_changed=price_changed,
        oi_only_changed=(not price_changed) and non_price_changed,
        changed_fields=tuple(dict.fromkeys(changed_fields).keys()),
        changed_contracts=changed_contracts,
        added_contracts=added.height,
        removed_contracts=removed.height,
    )


class UIRefreshService:
    def __init__(
        self,
        *,
        pipeline: QuantPipelineService,
        adapter: ProviderAdapter,
        provider_map: ProviderMap,
        cache: InMemoryQuoteCache,
        config: PipelineConfig,
    ) -> None:
        self._pipeline = pipeline
        self._adapter = adapter
        self._provider_map = provider_map
        self._cache = cache
        self._config = config
        self._loader = SnapshotBootstrapLoader(raw_root=config.parquet_root, derived_root=config.derived_parquet_root)

    def hydrate_latest_snapshot(self, symbol: str) -> RefreshResult:
        payload = self._loader.load_latest_symbol_payload(symbol)
        if payload is None:
            return RefreshResult(action="hydrate_missing", message="no stored snapshot found")
        self._publish_bootstrap_payload(payload)
        return RefreshResult(
            action="hydrate_latest",
            message=(
                f"loaded stored snapshot kind={payload.snapshot_kind} "
                f"trading_date={payload.trading_date or 'n/a'} batch={payload.batch_id}"
            ),
            snapshot_kind=payload.snapshot_kind,
            source_mode=payload.source_mode,
        )

    def load_chart_history(self, symbol: str, dataset: str) -> pl.DataFrame:
        persisted = self._loader.load_symbol_dataset_history(symbol, dataset)
        cache_history = self._cache.get_history_nowait(symbol, dataset)
        if cache_history.is_empty():
            return persisted
        if persisted.is_empty():
            return cache_history
        merged = pl.concat([persisted, cache_history], how="diagonal")
        unique_cols = [c for c in ("batch_id", "asof_ts", "expiration", "option_type", "strike", "engine_used") if c in merged.columns]
        if unique_cols:
            merged = merged.unique(subset=unique_cols, keep="last")
        sort_cols = [c for c in ("asof_ts", "expiration", "strike") if c in merged.columns]
        if sort_cols:
            merged = merged.sort(sort_cols)
        return merged

    def refresh_for_ui(self, symbol: str) -> RefreshResult:
        now_et = datetime.now(ZoneInfo(self._config.snapshot_timezone))
        current = self._cache.get_snapshot_nowait(symbol)

        if now_et.strftime("%H:%M") < self._config.market_close_freeze_time and current is not None:
            return RefreshResult(
                action="live_active",
                message="live session is active; waiting for the next live batch instead of forcing a manual refresh",
                snapshot_kind=current.snapshot_kind,
                source_mode=current.source_mode,
            )

        if self._config.market_close_freeze_time <= now_et.strftime("%H:%M") < self._config.final_prices_refresh_time:
            hydrate = self.hydrate_latest_snapshot(symbol)
            hydrate.message = (
                f"{hydrate.message}; final-price refresh window opens at {self._config.final_prices_refresh_time} "
                f"{self._config.snapshot_timezone}"
            )
            return hydrate

        return asyncio.run(self._refresh_async(symbol, current))

    async def _refresh_async(self, symbol: str, current: SymbolSnapshot | None) -> RefreshResult:
        ingestor = SnapshotIngestor(adapter=self._adapter, provider_map=self._provider_map)
        latest = await ingestor.fetch_snapshot(symbol)
        if latest.is_empty():
            return RefreshResult(action="refresh_empty", message="provider returned an empty snapshot")

        diff = compare_refresh_frames(
            current.raw if current is not None else pl.DataFrame(),
            latest,
            abs_tol=self._config.price_change_abs_tol,
        )
        now_et = datetime.now(ZoneInfo(self._config.snapshot_timezone))
        hhmm = now_et.strftime("%H:%M")
        parent_batch_id = current.batch_id if current is not None else ""

        if diff.price_space_changed or current is None or not current.is_final_for_day:
            snapshot_kind = "eod_final_refresh" if hhmm >= self._config.final_prices_refresh_time else "manual_snapshot"
            source_mode = "ui_refresh_final" if hhmm >= self._config.final_prices_refresh_time else "ui_refresh_manual"
            await self._pipeline.process_snapshot_frame(
                latest,
                snapshot_kind=snapshot_kind,
                source_mode=source_mode,
                is_final_for_day=hhmm >= self._config.final_prices_refresh_time,
                parent_batch_id=parent_batch_id,
                flush_calibration_diagnostics=True,
            )
            return RefreshResult(
                action="recomputed",
                message=(
                    f"recomputed {snapshot_kind}; price-sensitive fields changed={','.join(diff.changed_fields) or 'contract_set'} "
                    f"contracts={diff.changed_contracts} added={diff.added_contracts} removed={diff.removed_contracts}"
                ),
                snapshot_kind=snapshot_kind,
                source_mode=source_mode,
                price_space_changed=True,
                changed_fields=diff.changed_fields,
                changed_contracts=diff.changed_contracts,
                added_contracts=diff.added_contracts,
                removed_contracts=diff.removed_contracts,
            )

        if hhmm >= self._config.oi_refresh_time and diff.oi_only_changed:
            await self._pipeline.process_snapshot_frame(
                latest,
                snapshot_kind="eod_oi_refresh",
                source_mode="ui_refresh_oi",
                is_final_for_day=False,
                parent_batch_id=parent_batch_id,
                flush_calibration_diagnostics=False,
            )
            return RefreshResult(
                action="oi_refresh",
                message=(
                    f"applied oi refresh without recomputing greeks; changed fields={','.join(diff.changed_fields)} "
                    f"contracts={diff.changed_contracts}"
                ),
                snapshot_kind="eod_oi_refresh",
                source_mode="ui_refresh_oi",
                oi_only_changed=True,
                changed_fields=diff.changed_fields,
                changed_contracts=diff.changed_contracts,
            )

        if diff.oi_only_changed:
            hydrate = self.hydrate_latest_snapshot(symbol)
            hydrate.message = (
                f"{hydrate.message}; non-price fields changed ({','.join(diff.changed_fields)}), "
                f"but OI refresh opens at {self._config.oi_refresh_time} {self._config.snapshot_timezone}"
            )
            hydrate.oi_only_changed = True
            hydrate.changed_fields = diff.changed_fields
            hydrate.changed_contracts = diff.changed_contracts
            return hydrate

        return RefreshResult(
            action="no_change",
            message="no price-sensitive refresh needed; stored final snapshot already matches current provider price state",
            snapshot_kind=current.snapshot_kind if current is not None else "",
            source_mode=current.source_mode if current is not None else "",
        )

    def _publish_bootstrap_payload(self, payload: BatchPayload) -> None:
        asyncio.run(self._cache.publish_batch(payload))
        self._cache.append_history(payload.symbol, "raw", payload.raw)
        self._cache.append_history(payload.symbol, "greeks", payload.greeks)
        self._cache.append_history(payload.symbol, "ssvi", payload.ssvi)
        self._cache.append_history(payload.symbol, "dispatch", payload.dispatch)
        self._cache.append_history(payload.symbol, "parity", payload.parity)
        self._cache.append_history(payload.symbol, "parity_detail", payload.parity_detail)
        self._cache.append_history(payload.symbol, "diagnostics", payload.calibration_diag)

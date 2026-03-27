from __future__ import annotations

import asyncio
import gc
import importlib.util
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import polars as pl
import psutil

from flow_core.config.models import PipelineConfig, ProviderMap
from flow_core.ingestion.live_worker import LiveIngestionWorker
from flow_core.ingestion.providers.base import ProviderAdapter
from flow_core.ingestion.snapshot import SnapshotIngestor
from flow_core.orchestration.calibration_diagnostics import make_calibration_diagnostic_row, make_ssvi_diagnostic_row
from flow_core.orchestration.cache import InMemoryQuoteCache
from flow_core.orchestration.quote_quality import build_quote_quality
from flow_core.orchestration.runtime_metrics import make_runtime_metrics_row
from flow_core.orchestration.short_expiry_scanner import build_short_expiry_scanner_bundle
from flow_core.orchestration.scheduler import run_eod_scheduler
from flow_core.orchestration.surface_diagnostics import build_surface_diagnostics
from flow_core.orchestration.state_store import BatchPayload
from flow_core.quant.deamericanization import evaluate_parity_diagnostics
from flow_core.quant.dispatch import build_dispatch_summary
from flow_core.quant.market_inputs import HybridDividendSource, TBillRateCurve
from flow_core.quant.bs import BSInput, price_euro_bs
from flow_core.quant.model_greeks import compute_model_greeks
from flow_core.quant.models import SSVIResult
from flow_core.quant.routed_greeks import compute_routed_greeks
from flow_core.quant.routing import annotate_with_routing
from flow_core.quant.ssvi import calibrate_ssvi, calibrate_ssvi_cpp, ssvi_implied_vol_at
from flow_core.storage.parquet_store import BufferedParquetWriter, ParquetStore

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _trading_date_str(asof_ts: datetime, tz_name: str) -> str:
    try:
        return asof_ts.astimezone(ZoneInfo(tz_name)).date().isoformat()
    except Exception:
        return asof_ts.date().isoformat()


def _ensure_col(frame: pl.DataFrame, name: str, value) -> pl.DataFrame:  # noqa: ANN001
    if name in frame.columns:
        return frame
    return frame.with_columns(pl.lit(value).alias(name))


def _tag_frame(
    frame: pl.DataFrame,
    *,
    batch_id: str,
    symbol: str,
    asof_ts: datetime,
    trading_date: str,
    snapshot_kind: str,
    source_mode: str,
) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    out = frame
    out = _ensure_col(out, "symbol", symbol)
    out = _ensure_col(out, "asof_ts", asof_ts)
    out = _ensure_col(out, "batch_id", batch_id)
    out = _ensure_col(out, "trading_date", trading_date)
    out = _ensure_col(out, "snapshot_kind", snapshot_kind)
    out = _ensure_col(out, "source_mode", source_mode)
    return out


def _run_ssvi_fit(
    slice_frame: pl.DataFrame,
    *,
    config: PipelineConfig,
    init_guess: dict[str, float] | None,
    fit_space: str,
    weight_col: str,
) -> tuple[SSVIResult, str, str]:
    backend_used = "python"
    failure_reason = ""
    primary: SSVIResult | None = None
    if config.ssvi_backend in {"cpp", "auto"}:
        try:
            primary_cpp, meta_cpp = calibrate_ssvi_cpp(
                slice_frame,
                init_guess=init_guess,
                fit_space=fit_space,
                rate=config.parity_rate,
                dividend=config.parity_dividend,
                vol_col="implied_vol_input",
                weight_col=weight_col,
            )
            primary = primary_cpp
            backend_used = "cpp"
            failure_reason = str(meta_cpp.get("reason", ""))
            if not primary.success and config.runtime_mode != "live_strict" and config.ssvi_backend == "auto":
                fallback_reason = failure_reason or "cpp_nonconverged"
                primary = calibrate_ssvi(
                    slice_frame,
                    init_guess=init_guess,
                    fit_space=fit_space,
                    rate=config.parity_rate,
                    dividend=config.parity_dividend,
                    vol_col="implied_vol_input",
                    weight_col=weight_col,
                )
                backend_used = "python"
                failure_reason = f"fallback:{fallback_reason}"
        except Exception as exc:
            if config.runtime_mode == "live_strict":
                primary = SSVIResult(
                    a=0.0,
                    b=0.0,
                    rho=0.0,
                    m=0.0,
                    sigma=0.0,
                    objective=float("inf"),
                    success=False,
                    iterations=0,
                    durrleman_pass=False,
                )
                backend_used = "cpp"
                failure_reason = f"cpp_error:{exc}"
            else:
                primary = calibrate_ssvi(
                    slice_frame,
                    init_guess=init_guess,
                    fit_space=fit_space,
                    rate=config.parity_rate,
                    dividend=config.parity_dividend,
                    vol_col="implied_vol_input",
                    weight_col=weight_col,
                )
                backend_used = "python"
                failure_reason = f"fallback:cpp_error:{exc}"
    if primary is None:
        primary = calibrate_ssvi(
            slice_frame,
            init_guess=init_guess,
            fit_space=fit_space,
            rate=config.parity_rate,
            dividend=config.parity_dividend,
            vol_col="implied_vol_input",
            weight_col=weight_col,
        )
        backend_used = "python"
    return primary, backend_used, failure_reason


def _build_ssvi_outputs(
    calibration_input: pl.DataFrame,
    *,
    symbol: str,
    asof_ts: datetime,
    batch_id: str,
    trading_date: str,
    snapshot_kind: str,
    source_mode: str,
    config: PipelineConfig,
) -> tuple[pl.DataFrame, list[pl.DataFrame], str | None]:
    required = {"expiration", "implied_vol_input", "weight_uniform", "weight_atm", "weight_atm_corridor_tightness"}
    if calibration_input.is_empty() or not required.issubset(calibration_input.columns):
        return pl.DataFrame(), [], None

    init_guess = {"a": 0.01, "b": 0.1, "rho": -0.2, "m": 0.0, "sigma": 0.25} if config.ssvi_warm_start else None
    summary_rows: list[dict[str, object]] = []
    calibration_frames: list[pl.DataFrame] = []
    ssvi_error: str | None = None
    weight_modes = [
        ("uniform", "weight_uniform"),
        ("atm_only", "weight_atm"),
        ("atm_x_corridor_tightness", "weight_atm_corridor_tightness"),
    ]

    for expiration, slice_frame in calibration_input.partition_by(["expiration"], as_dict=True).items():
        if isinstance(expiration, tuple):
            expiration = expiration[0]
        option_type = (
            str(slice_frame["surface_source"][0])
            if "surface_source" in slice_frame.columns and slice_frame.height > 0
            else "surface"
        )
        if slice_frame.height < 3:
            failure = "insufficient_clean_core"
            if ssvi_error is None and config.runtime_mode == "live_strict":
                ssvi_error = f"{failure} symbol={symbol} expiration={expiration} option_type={option_type}"
            summary_rows.append(
                {
                    "expiration": expiration,
                    "option_type": option_type,
                    "weight_mode": "atm_only",
                    "fit_space": config.ssvi_fit_space,
                    "objective": float("inf"),
                    "iterations": 0,
                    "success": False,
                    "durrleman_pass": False,
                    "a": 0.0,
                    "b": 0.0,
                    "rho": 0.0,
                    "m": 0.0,
                    "sigma": 0.0,
                    "compare_fit_space": "",
                    "compare_objective": float("nan"),
                    "compare_iterations": 0,
                    "compare_success": False,
                    "backend_used": "",
                    "runtime_mode": config.runtime_mode,
                    "failure_reason": failure,
                }
            )
            calibration_frames.append(
                make_calibration_diagnostic_row(
                    symbol=symbol,
                    asof_ts=asof_ts,
                    expiration=expiration,
                    batch_id=batch_id,
                    snapshot_kind=snapshot_kind,
                    source_mode=source_mode,
                    trading_date=trading_date,
                    model_id=f"ssvi_{option_type}_atm_only_{config.ssvi_fit_space}",
                    backend_used="",
                    runtime_mode=config.runtime_mode,
                    converged=False,
                    iterations=0,
                    sse_final=float("inf"),
                    durrleman_pass=False,
                    failure_reason=failure,
                )
            )
            continue

        for weight_mode, weight_col in weight_modes:
            result, backend_used, failure_reason = _run_ssvi_fit(
                slice_frame,
                config=config,
                init_guess=init_guess,
                fit_space=config.ssvi_fit_space,
                weight_col=weight_col,
            )
            compare: SSVIResult | None = None
            compare_space = ""
            if (
                weight_mode == "atm_only"
                and config.ssvi_enable_space_compare
                and config.ssvi_compare_fit_space != config.ssvi_fit_space
            ):
                compare_space = config.ssvi_compare_fit_space
                compare = calibrate_ssvi(
                    slice_frame,
                    init_guess=init_guess,
                    fit_space=compare_space,
                    rate=config.parity_rate,
                    dividend=config.parity_dividend,
                    vol_col="implied_vol_input",
                    weight_col=weight_col,
                )
                calibration_frames.append(
                    make_ssvi_diagnostic_row(
                        symbol=symbol,
                        asof_ts=asof_ts,
                        expiration=expiration,
                        batch_id=batch_id,
                        snapshot_kind=snapshot_kind,
                        source_mode=source_mode,
                        trading_date=trading_date,
                        model_id=f"ssvi_{option_type}_{weight_mode}_{compare_space}",
                        result=compare,
                        backend_used="python",
                        runtime_mode=config.runtime_mode,
                        failure_reason="",
                    )
                )

            summary_rows.append(
                {
                    "expiration": expiration,
                    "option_type": option_type,
                    "weight_mode": weight_mode,
                    "fit_space": config.ssvi_fit_space,
                    "objective": result.objective,
                    "iterations": result.iterations,
                    "success": result.success,
                    "durrleman_pass": result.durrleman_pass,
                    "a": result.a,
                    "b": result.b,
                    "rho": result.rho,
                    "m": result.m,
                    "sigma": result.sigma,
                    "compare_fit_space": compare_space,
                    "compare_objective": compare.objective if compare is not None else float("nan"),
                    "compare_iterations": compare.iterations if compare is not None else 0,
                    "compare_success": compare.success if compare is not None else False,
                    "backend_used": backend_used,
                    "runtime_mode": config.runtime_mode,
                    "failure_reason": failure_reason,
                }
            )
            calibration_frames.append(
                make_ssvi_diagnostic_row(
                    symbol=symbol,
                    asof_ts=asof_ts,
                    expiration=expiration,
                    batch_id=batch_id,
                    snapshot_kind=snapshot_kind,
                    source_mode=source_mode,
                    trading_date=trading_date,
                    model_id=f"ssvi_{option_type}_{weight_mode}_{config.ssvi_fit_space}",
                    result=result,
                    backend_used=backend_used,
                    runtime_mode=config.runtime_mode,
                    failure_reason=failure_reason,
                )
            )
            if weight_mode == "atm_only" and config.ssvi_fit_space == "log" and not result.success and ssvi_error is None:
                ssvi_error = (
                    f"primary_ssvi_log_fit_failed symbol={symbol} expiration={expiration} option_type={option_type} "
                    f"objective={result.objective:.6e} iterations={result.iterations}"
                )

    ssvi_summary = pl.DataFrame(summary_rows) if summary_rows else pl.DataFrame()
    return ssvi_summary, calibration_frames, ssvi_error


def _select_primary_ssvi_frame(ssvi_summary: pl.DataFrame) -> pl.DataFrame:
    if ssvi_summary.is_empty():
        return pl.DataFrame()
    priority = {"atm_x_corridor_tightness": 0, "atm_only": 1, "uniform": 2}
    rows: list[dict[str, object]] = []
    for group_key, frame in ssvi_summary.partition_by(["expiration"], as_dict=True).items():
        expiration = group_key[0] if isinstance(group_key, tuple) else group_key
        ordered = sorted(
            frame.to_dicts(),
            key=lambda row: (
                0 if bool(row.get("success")) else 1,
                priority.get(str(row.get("weight_mode", "")), 99),
                float(row.get("objective", float("inf")) or float("inf")),
            ),
        )
        chosen = dict(ordered[0])
        chosen["expiration"] = expiration
        rows.append(chosen)
    return pl.DataFrame(rows)


def _evaluate_ssvi_surface_points(points: pl.DataFrame, primary_ssvi: pl.DataFrame, *, fit_space: str) -> pl.DataFrame:
    if points.is_empty():
        return points
    if primary_ssvi.is_empty():
        return points.with_columns(
            pl.lit(None, dtype=pl.Float64).alias("ssvi_vol"),
            pl.lit(None, dtype=pl.Float64).alias("ssvi_vol_lower"),
            pl.lit(None, dtype=pl.Float64).alias("ssvi_vol_upper"),
            pl.lit(False).alias("ssvi_vol_outside_band"),
            pl.lit(None, dtype=pl.Float64).alias("ssvi_euro_price"),
            pl.lit(False).alias("euro_price_inside_band"),
            pl.lit("").alias("model_batch_role"),
        )
    models = {}
    for row in primary_ssvi.to_dicts():
        models[str(row.get("expiration"))] = SSVIResult(
            a=float(row.get("a", 0.0) or 0.0),
            b=float(row.get("b", 0.0) or 0.0),
            rho=float(row.get("rho", 0.0) or 0.0),
            m=float(row.get("m", 0.0) or 0.0),
            sigma=float(row.get("sigma", 0.0) or 0.0),
            objective=float(row.get("objective", float("nan")) or float("nan")),
            success=bool(row.get("success", False)),
            iterations=int(row.get("iterations", 0) or 0),
            durrleman_pass=bool(row.get("durrleman_pass", True)),
        )
    out_rows: list[dict[str, object]] = []
    for row in points.to_dicts():
        out = dict(row)
        params = models.get(str(row.get("expiration")))
        ssvi_vol = float("nan")
        euro_price = float("nan")
        if params is not None and params.success:
            ssvi_vol = ssvi_implied_vol_at(
                strike=float(row.get("strike", float("nan")) or float("nan")),
                spot=float(row.get("underlying_price", float("nan")) or float("nan")),
                tau=float(row.get("tau_years", float("nan")) or float("nan")),
                rate=float(row.get("rate_used", 0.0) or 0.0),
                dividend=0.0,
                params=params,
                fit_space=fit_space,
            )
            if math.isfinite(ssvi_vol):
                euro_price = price_euro_bs(
                    BSInput(
                        spot=float(row.get("underlying_price", float("nan")) or float("nan")),
                        strike=float(row.get("strike", float("nan")) or float("nan")),
                        rate=float(row.get("rate_used", 0.0) or 0.0),
                        dividend=0.0,
                        tau=float(row.get("tau_years", float("nan")) or float("nan")),
                        vol=float(ssvi_vol),
                        is_call=str(row.get("option_type", "")).lower() == "call",
                    )
                ).price
        lower = float(row.get("iv_bid", float("nan")) or float("nan"))
        upper = float(row.get("iv_ask", float("nan")) or float("nan"))
        euro_bid = float(row.get("euro_price_bid", float("nan")) or float("nan"))
        euro_ask = float(row.get("euro_price_ask", float("nan")) or float("nan"))
        out.update(
            {
                "ssvi_vol": float(ssvi_vol),
                "ssvi_vol_lower": lower,
                "ssvi_vol_upper": upper,
                "ssvi_vol_outside_band": bool(
                    math.isfinite(ssvi_vol)
                    and math.isfinite(lower)
                    and math.isfinite(upper)
                    and (ssvi_vol < lower - 1e-9 or ssvi_vol > upper + 1e-9)
                ),
                "ssvi_euro_price": float(euro_price),
                "euro_price_inside_band": bool(
                    math.isfinite(euro_price)
                    and math.isfinite(euro_bid)
                    and math.isfinite(euro_ask)
                    and euro_bid - 1e-9 <= euro_price <= euro_ask + 1e-9
                ),
                "model_batch_role": "selected_batch",
            }
        )
        out_rows.append(out)
    return pl.DataFrame(out_rows)


def _snapshot_catalog_row(
    *,
    batch_id: str,
    symbol: str,
    asof_ts: datetime,
    trading_date: str,
    snapshot_kind: str,
    source_mode: str,
    is_final_for_day: bool,
    parent_batch_id: str,
    raw_rows: int,
    greeks_rows: int,
    diagnostics_rows: int,
    updated_at_utc: datetime,
) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "batch_id": batch_id,
                "symbol": symbol,
                "asof_ts": asof_ts,
                "updated_at_utc": updated_at_utc,
                "trading_date": trading_date,
                "snapshot_kind": snapshot_kind,
                "source_mode": source_mode,
                "is_final_for_day": is_final_for_day,
                "parent_batch_id": parent_batch_id,
                "raw_rows": raw_rows,
                "greeks_rows": greeks_rows,
                "diagnostics_rows": diagnostics_rows,
            }
        ]
    )


def _oi_refresh_delta(parent_raw: pl.DataFrame, refresh_raw: pl.DataFrame, *, parent_batch_id: str, trading_date: str) -> pl.DataFrame:
    if parent_raw.is_empty() or refresh_raw.is_empty():
        return pl.DataFrame()
    join_keys = [c for c in ("symbol", "expiration", "option_type", "strike") if c in parent_raw.columns and c in refresh_raw.columns]
    if len(join_keys) != 4:
        return pl.DataFrame()
    delta_cols = [c for c in ("open_interest", "volume") if c in refresh_raw.columns and c in parent_raw.columns]
    if not delta_cols:
        return pl.DataFrame()
    lhs = refresh_raw.select(join_keys + delta_cols)
    rhs = parent_raw.select(join_keys + delta_cols).rename({c: f"{c}__parent" for c in delta_cols})
    joined = lhs.join(rhs, on=join_keys, how="inner")
    changed = None
    for col in delta_cols:
        expr = pl.col(col) != pl.col(f"{col}__parent")
        changed = expr if changed is None else (changed | expr)
    if changed is None:
        return pl.DataFrame()
    joined = joined.filter(changed)
    if joined.is_empty():
        return pl.DataFrame()
    return joined.with_columns(
        pl.lit(parent_batch_id).alias("parent_batch_id"),
        pl.lit(trading_date).alias("trading_date"),
    ).drop([f"{c}__parent" for c in delta_cols])


def _apply_oi_refresh_to_parent(parent_raw: pl.DataFrame, delta: pl.DataFrame) -> pl.DataFrame:
    if parent_raw.is_empty() or delta.is_empty():
        return parent_raw
    join_keys = [c for c in ("symbol", "expiration", "option_type", "strike") if c in parent_raw.columns and c in delta.columns]
    delta_cols = [c for c in ("open_interest", "volume") if c in delta.columns]
    if len(join_keys) != 4 or not delta_cols:
        return parent_raw
    delta_renamed = delta.select(join_keys + delta_cols).rename({c: f"{c}__refresh" for c in delta_cols})
    out = parent_raw.join(delta_renamed, on=join_keys, how="left")
    for col in delta_cols:
        out = out.with_columns(pl.coalesce(pl.col(f"{col}__refresh"), pl.col(col)).alias(col)).drop(f"{col}__refresh")
    return out


@dataclass(slots=True)
class QuantPipelineService:
    adapter: ProviderAdapter
    provider_map: ProviderMap
    config: PipelineConfig
    cache: InMemoryQuoteCache
    parquet_store: ParquetStore
    derived_store: ParquetStore | None = None
    rate_curve: TBillRateCurve | None = None
    dividend_source: HybridDividendSource | None = None
    buffered_writer: BufferedParquetWriter | None = None
    _last_diag_flush_ts: dict[str, float] = field(default_factory=dict, init=False, repr=False)
    _soft_exceed_count: int = field(default=0, init=False, repr=False)
    _last_trim_ts: float = field(default=0.0, init=False, repr=False)
    _nonconv_count: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.config.runtime_mode == "live_strict":
            need_cpp = self.config.ssvi_backend in {"cpp", "auto"} or self.config.fdm_backend in {"cpp", "auto"}
            if need_cpp and importlib.util.find_spec("quantcore") is None:
                raise RuntimeError(
                    "runtime_mode=live_strict requires quantcore module for configured backends "
                    f"(ssvi_backend={self.config.ssvi_backend}, fdm_backend={self.config.fdm_backend})"
                )
            if need_cpp:
                import quantcore  # type: ignore

                missing: list[str] = []
                if self.config.ssvi_backend in {"cpp", "auto"} and not hasattr(quantcore, "calibrate_ssvi_slice"):
                    missing.append("calibrate_ssvi_slice")
                if self.config.fdm_backend in {"cpp", "auto"} and not hasattr(quantcore, "fdm_cn_log_greeks"):
                    missing.append("fdm_cn_log_greeks")
                if missing:
                    raise RuntimeError(
                        "runtime_mode=live_strict quantcore missing required entrypoints: "
                        + ",".join(missing)
                    )
        if self.buffered_writer is None:
            self.buffered_writer = BufferedParquetWriter(
                raw_store=self.parquet_store,
                derived_store=self.derived_store,
                flush_interval_sec=self.config.parquet_flush_interval_sec,
                flush_max_rows=self.config.parquet_flush_max_rows,
            )

    async def run_live(
        self,
        symbol: str,
        expiration: str | None = None,
        expiration_resolver: Callable[[], str | None] | None = None,
        status_callback: Callable[[dict[str, object]], None] | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        event = stop_event or asyncio.Event()
        configured_expiration = expiration
        if configured_expiration is None:
            if self.config.live_expiry_scope == "selected" and self.config.live_selected_expiries:
                configured_expiration = ",".join(self.config.live_selected_expiries)
            else:
                configured_expiration = self.config.live_expiry_scope

        def _resolve_expiration() -> str | None:
            if expiration_resolver is not None:
                requested = expiration_resolver()
                if requested:
                    return requested
            return configured_expiration

        worker = LiveIngestionWorker(
            adapter=self.adapter,
            provider_map=self.provider_map,
            config=self.config,
            on_batch=self._on_live_batch,
            status_callback=status_callback,
        )
        flush_task = asyncio.create_task(self.buffered_writer.run_periodic_flush(event))
        mem_task = asyncio.create_task(self._run_memory_monitor(event))
        final_task = asyncio.create_task(
            run_eod_scheduler(
                lambda: self._capture_final_and_stop(symbol, event),
                target_time=self.config.market_close_freeze_time,
                timezone=self.config.snapshot_timezone,
            )
        )
        try:
            await worker.run(
                symbol=symbol,
                expiration=configured_expiration,
                expiration_resolver=_resolve_expiration,
                stop_event=event,
            )
        finally:
            worker.stop()
            event.set()
            for task in (flush_task, mem_task, final_task):
                task.cancel()
            await asyncio.gather(flush_task, mem_task, final_task, return_exceptions=True)
            await self.buffered_writer.flush_all()

    async def capture_snapshot(
        self,
        symbol: str,
        *,
        snapshot_kind: str = "manual_snapshot",
        source_mode: str = "manual_snapshot",
        is_final_for_day: bool = False,
        parent_batch_id: str = "",
    ) -> pl.DataFrame:
        ingestor = SnapshotIngestor(adapter=self.adapter, provider_map=self.provider_map)
        frame = await ingestor.fetch_snapshot(symbol)
        await self._process_batch(
            frame,
            flush_calibration_diagnostics=True,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
            is_final_for_day=is_final_for_day,
            parent_batch_id=parent_batch_id,
        )
        return frame

    async def process_snapshot_frame(
        self,
        frame: pl.DataFrame,
        *,
        snapshot_kind: str,
        source_mode: str,
        is_final_for_day: bool = False,
        parent_batch_id: str = "",
        flush_calibration_diagnostics: bool = True,
    ) -> None:
        await self._process_batch(
            frame,
            flush_calibration_diagnostics=flush_calibration_diagnostics,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
            is_final_for_day=is_final_for_day,
            parent_batch_id=parent_batch_id,
        )

    async def _on_live_batch(self, frame: pl.DataFrame) -> None:
        await self._process_batch(
            frame,
            flush_calibration_diagnostics=False,
            snapshot_kind="live_batch",
            source_mode="live_worker",
            is_final_for_day=False,
            parent_batch_id="",
        )

    async def _capture_final_and_stop(self, symbol: str, stop_event: asyncio.Event) -> None:
        if stop_event.is_set():
            return
        current = self.cache.get_snapshot_nowait(symbol)
        parent_batch_id = current.batch_id if current is not None else ""
        await self.capture_snapshot(
            symbol,
            snapshot_kind="eod_final",
            source_mode="live_final",
            is_final_for_day=True,
            parent_batch_id=parent_batch_id,
        )
        stop_event.set()

    async def _process_batch(
        self,
        frame: pl.DataFrame,
        flush_calibration_diagnostics: bool,
        *,
        snapshot_kind: str,
        source_mode: str,
        is_final_for_day: bool,
        parent_batch_id: str,
    ) -> None:
        if frame.is_empty():
            return

        latency: dict[str, float] = {"ingestion_ms": 0.0}
        batch_start = time.perf_counter()
        asof_ts = frame["asof_ts"][0] if "asof_ts" in frame.columns else _now_utc()
        if not isinstance(asof_ts, datetime):
            asof_ts = _now_utc()
        symbol = str(frame["symbol"][0]) if "symbol" in frame.columns else ""
        batch_id = f"{symbol}:{int(_now_utc().timestamp() * 1e9)}:{uuid4().hex[:8]}"
        trading_date = _trading_date_str(asof_ts, self.config.snapshot_timezone)

        t0 = time.perf_counter()
        routed = annotate_with_routing(frame)
        routed = _tag_frame(
            routed,
            batch_id=batch_id,
            symbol=symbol,
            asof_ts=asof_ts,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
        )
        latency["mapping_ms"] = 0.0
        latency["routing_ms"] = (time.perf_counter() - t0) * 1000.0
        latency["route_ms"] = latency["routing_ms"]

        status: dict[str, str | bool | float] = {
            "ssvi_fail": False,
            "runtime_mode": self.config.runtime_mode,
            "snapshot_kind": snapshot_kind,
            "source_mode": source_mode,
        }

        if snapshot_kind == "eod_oi_refresh":
            parent_snapshot = self.cache.get_snapshot_nowait(symbol)
            base_parent_id = parent_batch_id or (parent_snapshot.batch_id if parent_snapshot is not None else "")
            delta = _oi_refresh_delta(
                parent_snapshot.raw if parent_snapshot is not None else pl.DataFrame(),
                routed,
                parent_batch_id=base_parent_id,
                trading_date=trading_date,
            )
            merged_raw = _apply_oi_refresh_to_parent(parent_snapshot.raw, delta) if parent_snapshot is not None else routed
            merged_raw = _tag_frame(
                merged_raw,
                batch_id=batch_id,
                symbol=symbol,
                asof_ts=asof_ts,
                trading_date=trading_date,
                snapshot_kind=snapshot_kind,
                source_mode=source_mode,
            )
            payload = BatchPayload(
                symbol=symbol,
                batch_id=batch_id,
                version_hint=None,
                updated_at_utc=_now_utc(),
                raw=merged_raw,
                greeks=parent_snapshot.greeks if parent_snapshot is not None else pl.DataFrame(),
                ssvi=parent_snapshot.ssvi if parent_snapshot is not None else pl.DataFrame(),
                dispatch=parent_snapshot.dispatch if parent_snapshot is not None else pl.DataFrame(),
                parity=parent_snapshot.parity if parent_snapshot is not None else pl.DataFrame(),
                parity_detail=parent_snapshot.parity_detail if parent_snapshot is not None else pl.DataFrame(),
                calibration_diag=parent_snapshot.calibration_diag_tail if parent_snapshot is not None else pl.DataFrame(),
                trading_date=trading_date,
                snapshot_kind=snapshot_kind,
                source_mode=source_mode,
                is_final_for_day=bool(parent_snapshot.is_final_for_day) if parent_snapshot is not None else False,
                parent_batch_id=base_parent_id,
                latency_ms=latency,
                status={**status, "oi_refresh": True},
            )
            t0 = time.perf_counter()
            version = await self.cache.publish_batch(payload)
            latency["ui_bridge_ms"] = (time.perf_counter() - t0) * 1000.0
            self.cache.append_history(symbol, "raw", merged_raw)
            t0 = time.perf_counter()
            await self.buffered_writer.append_raw(routed)
            if self.derived_store is not None:
                if not delta.is_empty():
                    delta = _tag_frame(
                        delta,
                        batch_id=batch_id,
                        symbol=symbol,
                        asof_ts=asof_ts,
                        trading_date=trading_date,
                        snapshot_kind=snapshot_kind,
                        source_mode=source_mode,
                    )
                    await self.buffered_writer.append_derived(delta, dataset="oi_refresh_deltas", partition_col="symbol")
                catalog = _snapshot_catalog_row(
                    batch_id=batch_id,
                    symbol=symbol,
                    asof_ts=asof_ts,
                    trading_date=trading_date,
                    snapshot_kind=snapshot_kind,
                    source_mode=source_mode,
                    is_final_for_day=False,
                    parent_batch_id=base_parent_id,
                    raw_rows=merged_raw.height,
                    greeks_rows=payload.greeks.height,
                    diagnostics_rows=payload.calibration_diag.height,
                    updated_at_utc=payload.updated_at_utc,
                )
                await self.buffered_writer.append_derived(catalog, dataset="snapshot_catalog", partition_col="symbol")
            latency["persist_ms"] = (time.perf_counter() - t0) * 1000.0
            latency["total_ms"] = (time.perf_counter() - batch_start) * 1000.0
            logger.info(
                "pipeline_batch_ok symbol=%s version=%s snapshot_kind=%s latency_ms=%.2f",
                symbol,
                version,
                snapshot_kind,
                latency["total_ms"],
            )
            return

        t0 = time.perf_counter()
        quote_quality = build_quote_quality(
            routed,
            rate=self.config.parity_rate,
            rate_curve=self.rate_curve,
            dividend_source=self.dividend_source,
        )
        quote_quality_points = _tag_frame(
            quote_quality.points,
            batch_id=batch_id,
            symbol=symbol,
            asof_ts=asof_ts,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
        )
        join_keys = [c for c in ("symbol", "contract_symbol", "expiration", "option_type", "strike") if c in routed.columns and c in quote_quality_points.columns]
        routed_pricing = routed.join(
            quote_quality_points.select(
                [
                    c
                    for c in (
                        *join_keys,
                        "iv_ref",
                        "weight_uniform",
                        "weight_atm",
                        "weight_corridor_tightness",
                        "weight_atm_corridor_tightness",
                        "market_mid",
                        "eligible",
                        "drop_reason",
                        "strip_shape_fail",
                        "euro_price_bid",
                        "euro_price_ask",
                        "euro_price_ref",
                        "dual_delta_bid",
                        "dual_delta_ask",
                        "dual_delta_ref",
                        "price_second_derivative_ref",
                        "corridor_tightness",
                        "corridor_width",
                        "vendor_iv_ref",
                        "iv_bid",
                        "iv_ask",
                        "fit_region",
                        "is_atm_blend",
                        "blend_source",
                        "eligible_for_fit",
                        "excluded_from_fit_reason",
                    )
                    if c in quote_quality_points.columns
                ]
            ),
            on=join_keys,
            how="left",
        )
        latency["quote_quality_ms"] = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        greeks = compute_routed_greeks(
            routed_pricing,
            rate=self.config.parity_rate,
            dividend=self.config.parity_dividend,
            rate_curve=self.rate_curve,
            dividend_source=self.dividend_source,
            tree_steps=self.config.parity_tree_steps,
            rim_nodes=self.config.parity_rim_nodes,
            fdm_scheme="log",
            fdm_backend=self.config.fdm_backend,
            runtime_mode=self.config.runtime_mode,
        )
        greeks = _tag_frame(
            greeks,
            batch_id=batch_id,
            symbol=symbol,
            asof_ts=asof_ts,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
        )
        latency["pricing_ms"] = (time.perf_counter() - t0) * 1000.0
        latency["greeks_ms"] = latency["pricing_ms"]

        ssvi_summary = pl.DataFrame()
        calibration_frames: list[pl.DataFrame] = []
        ssvi_error: str | None = None

        t0 = time.perf_counter()
        ssvi_summary, calibration_frames, ssvi_error = _build_ssvi_outputs(
            quote_quality.calibration_input,
            symbol=symbol,
            asof_ts=asof_ts,
            batch_id=batch_id,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
            config=self.config,
        )
        if not ssvi_summary.is_empty():
            ssvi_summary = _tag_frame(
                ssvi_summary,
                batch_id=batch_id,
                symbol=symbol,
                asof_ts=asof_ts,
                trading_date=trading_date,
                snapshot_kind=snapshot_kind,
                source_mode=source_mode,
            )
        if ssvi_error is not None:
            status["ssvi_fail"] = True
            status["ssvi_error"] = ssvi_error
            streak = self._nonconv_count.get(symbol, 0) + 1
            self._nonconv_count[symbol] = streak
            if streak >= self.config.nonconvergence_alert_threshold:
                logger.warning("ssvi_nonconvergence_streak symbol=%s streak=%s err=%s", symbol, streak, ssvi_error)
        else:
            self._nonconv_count[symbol] = 0
        latency["calibration_ms"] = (time.perf_counter() - t0) * 1000.0
        latency["ssvi_ms"] = latency["calibration_ms"]

        primary_ssvi = _select_primary_ssvi_frame(ssvi_summary)
        surface_eval = _evaluate_ssvi_surface_points(
            quote_quality_points,
            primary_ssvi,
            fit_space=self.config.ssvi_fit_space,
        )
        model_greeks = pl.DataFrame()
        if not routed_pricing.is_empty() and not primary_ssvi.is_empty():
            ssvi_join = primary_ssvi.select(
                [
                    c
                    for c in (
                        "expiration",
                        "a",
                        "b",
                        "rho",
                        "m",
                        "sigma",
                        "objective",
                        "success",
                        "iterations",
                        "durrleman_pass",
                        "weight_mode",
                        "fit_space",
                    )
                    if c in primary_ssvi.columns
                ]
            ).rename(
                {
                    "a": "ssvi_a",
                    "b": "ssvi_b",
                    "rho": "ssvi_rho",
                    "m": "ssvi_m",
                    "sigma": "ssvi_sigma",
                    "objective": "ssvi_objective",
                    "success": "ssvi_success",
                    "iterations": "ssvi_iterations",
                    "durrleman_pass": "ssvi_durrleman_pass",
                    "weight_mode": "ssvi_weight_mode",
                    "fit_space": "ssvi_fit_space",
                }
            )
            model_input = routed_pricing.join(ssvi_join, on=[c for c in ("expiration",) if c in routed_pricing.columns and c in ssvi_join.columns], how="left")
            t0 = time.perf_counter()
            model_greeks = compute_model_greeks(
                model_input,
                dividend_source=self.dividend_source,
                tree_steps=self.config.parity_tree_steps,
                rim_nodes=self.config.parity_rim_nodes,
                fdm_scheme="log",
                fdm_backend=self.config.fdm_backend,
                runtime_mode=self.config.runtime_mode,
                fit_space=self.config.ssvi_fit_space,
            )
            model_greeks = _tag_frame(
                model_greeks,
                batch_id=batch_id,
                symbol=symbol,
                asof_ts=asof_ts,
                trading_date=trading_date,
                snapshot_kind=snapshot_kind,
                source_mode=source_mode,
            )
            latency["model_greeks_ms"] = (time.perf_counter() - t0) * 1000.0
        else:
            latency["model_greeks_ms"] = 0.0

        quality_join_keys = [c for c in ("symbol", "contract_symbol", "expiration", "option_type", "strike") if c in model_greeks.columns and c in surface_eval.columns]
        surface_input = model_greeks.join(
            surface_eval,
            on=quality_join_keys,
            how="left",
            suffix="_quality",
        )
        if not surface_input.is_empty():
            surface_input = surface_input.with_columns(
                pl.col("model_price").alias("ssvi_american_price"),
                ((pl.col("model_price") >= pl.col("bid") - 1e-9) & (pl.col("model_price") <= pl.col("ask") + 1e-9)).alias("american_price_inside_band"),
                pl.lit("model_greeks").alias("greeks_source"),
            )
        surface_bundle = build_surface_diagnostics(surface_input)
        surface_points = _tag_frame(
            surface_bundle.points,
            batch_id=batch_id,
            symbol=symbol,
            asof_ts=asof_ts,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
        )
        surface_diagnostics = _tag_frame(
            surface_bundle.summary.with_columns(
                pl.lit(self.config.runtime_mode).alias("runtime_mode"),
                pl.lit(self.config.ssvi_backend).alias("ssvi_backend"),
                pl.lit(self.config.fdm_backend).alias("fdm_backend"),
            ),
            batch_id=batch_id,
            symbol=symbol,
            asof_ts=asof_ts,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
        )
        previous_dealer_points = self.cache.get_history_nowait(symbol, "dealer_exposure_points")
        scanner_bundle = build_short_expiry_scanner_bundle(
            raw=routed,
            greeks=model_greeks if not model_greeks.is_empty() else greeks,
            surface_points=surface_points,
            previous_dealer_exposure_points=previous_dealer_points,
            focus_labels=list(self.config.live_focus_labels),
            symbol=symbol,
            asof_ts=asof_ts,
            batch_id=batch_id,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
        )
        focus_expiry_summary = _tag_frame(
            scanner_bundle.focus_expiry_summary,
            batch_id=batch_id,
            symbol=symbol,
            asof_ts=asof_ts,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
        )
        dealer_exposure_points = _tag_frame(
            scanner_bundle.dealer_exposure_points,
            batch_id=batch_id,
            symbol=symbol,
            asof_ts=asof_ts,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
        )
        flow_proxy_points = _tag_frame(
            scanner_bundle.flow_proxy_points,
            batch_id=batch_id,
            symbol=symbol,
            asof_ts=asof_ts,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
        )
        scanner_levels = _tag_frame(
            scanner_bundle.scanner_levels,
            batch_id=batch_id,
            symbol=symbol,
            asof_ts=asof_ts,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
        )

        t0 = time.perf_counter()
        dispatch = build_dispatch_summary(routed_pricing)
        dispatch = _tag_frame(
            dispatch,
            batch_id=batch_id,
            symbol=symbol,
            asof_ts=asof_ts,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
        )
        parity_summary = pl.DataFrame()
        parity_detail = pl.DataFrame()
        parity_solver_diag = pl.DataFrame()
        if ssvi_error is None:
            parity_summary, parity_detail, parity_solver_diag = evaluate_parity_diagnostics(
                routed_pricing,
                rate=self.config.parity_rate,
                dividend=self.config.parity_dividend,
                eep_mode=self.config.parity_eep_mode,
                max_pairs=self.config.parity_max_pairs,
                tree_steps=self.config.parity_tree_steps,
                luba_method=self.config.parity_luba_method,
                rim_nodes=self.config.parity_rim_nodes,
                return_solver_diagnostics=True,
            )
            parity_summary = _tag_frame(
                parity_summary,
                batch_id=batch_id,
                symbol=symbol,
                asof_ts=asof_ts,
                trading_date=trading_date,
                snapshot_kind=snapshot_kind,
                source_mode=source_mode,
            )
            parity_detail = _tag_frame(
                parity_detail,
                batch_id=batch_id,
                symbol=symbol,
                asof_ts=asof_ts,
                trading_date=trading_date,
                snapshot_kind=snapshot_kind,
                source_mode=source_mode,
            )
            if not parity_solver_diag.is_empty():
                for row in parity_solver_diag.to_dicts():
                    calibration_frames.append(
                        make_calibration_diagnostic_row(
                            symbol=symbol,
                            asof_ts=asof_ts,
                            expiration=row.get("expiration"),
                            batch_id=batch_id,
                            snapshot_kind=snapshot_kind,
                            source_mode=source_mode,
                            trading_date=trading_date,
                            model_id=str(row.get("model_id", "luba_rim")),
                            backend_used="python",
                            runtime_mode=self.config.runtime_mode,
                            converged=bool(row.get("converged", False)),
                            iterations=int(row.get("iterations", 0)),
                            sse_final=float(row.get("sse_final", float("inf"))),
                            durrleman_pass=bool(row.get("durrleman_pass", True)),
                            failure_reason="",
                            params=row.get("params") if isinstance(row.get("params"), dict) else None,
                        )
                    )
        latency["diag_ms"] = (time.perf_counter() - t0) * 1000.0

        if not greeks.is_empty() and {"engine_used", "success"}.issubset(greeks.columns):
            for row in greeks.filter(pl.col("engine_used").str.contains("fdm")).to_dicts():
                calibration_frames.append(
                    make_calibration_diagnostic_row(
                        symbol=symbol,
                        asof_ts=asof_ts,
                        expiration=row.get("expiration"),
                        batch_id=batch_id,
                        snapshot_kind=snapshot_kind,
                        source_mode=source_mode,
                        trading_date=trading_date,
                        model_id=str(row.get("engine_used", "fdm_cn_log")),
                        backend_used=str(row.get("backend_used", "")),
                        runtime_mode=self.config.runtime_mode,
                        converged=bool(row.get("success", False)),
                        iterations=0,
                        sse_final=0.0,
                        durrleman_pass=True,
                        failure_reason=str(row.get("error", "")),
                        jump_interp_mode=str(row.get("jump_interp_mode", "")),
                        params=None,
                    )
                )

        calibration = pl.concat(calibration_frames, how="vertical") if calibration_frames else pl.DataFrame()
        active_greeks = model_greeks if not model_greeks.is_empty() else greeks
        payload = BatchPayload(
            symbol=symbol,
            batch_id=batch_id,
            version_hint=None,
            updated_at_utc=_now_utc(),
            raw=routed,
            greeks=active_greeks,
            ssvi=ssvi_summary,
            dispatch=dispatch,
            parity=parity_summary,
            parity_detail=parity_detail,
            calibration_diag=calibration,
            trading_date=trading_date,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
            is_final_for_day=is_final_for_day,
            parent_batch_id=parent_batch_id,
            latency_ms=latency,
            status=status,
        )
        t0 = time.perf_counter()
        version = await self.cache.publish_batch(payload)
        latency["ui_bridge_ms"] = (time.perf_counter() - t0) * 1000.0
        self.cache.append_history(symbol, "raw", routed)
        self.cache.append_history(symbol, "greeks", greeks)
        self.cache.append_history(symbol, "model_greeks", model_greeks)
        self.cache.append_history(symbol, "ssvi", ssvi_summary)
        self.cache.append_history(symbol, "dispatch", dispatch)
        self.cache.append_history(symbol, "parity", parity_summary)
        self.cache.append_history(symbol, "parity_detail", parity_detail)
        self.cache.append_history(symbol, "diagnostics", calibration)
        self.cache.append_history(symbol, "quote_quality_points", quote_quality_points)
        self.cache.append_history(symbol, "surface_points", surface_points)
        self.cache.append_history(symbol, "surface_diagnostics", surface_diagnostics)
        self.cache.append_history(symbol, "focus_expiry_summary", focus_expiry_summary)
        self.cache.append_history(symbol, "dealer_exposure_points", dealer_exposure_points)
        self.cache.append_history(symbol, "flow_proxy_points", flow_proxy_points)
        self.cache.append_history(symbol, "scanner_levels", scanner_levels)

        t0 = time.perf_counter()
        await self.buffered_writer.append_raw(routed)
        if self.derived_store is not None:
            if not dispatch.is_empty():
                await self.buffered_writer.append_derived(dispatch, dataset="dispatch", partition_col="symbol")
            if not greeks.is_empty():
                await self.buffered_writer.append_derived(greeks, dataset="greeks", partition_col="symbol")
            if not model_greeks.is_empty():
                await self.buffered_writer.append_derived(model_greeks, dataset="model_greeks", partition_col="symbol")
            if not ssvi_summary.is_empty():
                await self.buffered_writer.append_derived(ssvi_summary, dataset="ssvi", partition_col="symbol")
            if not parity_summary.is_empty():
                await self.buffered_writer.append_derived(parity_summary, dataset="parity", partition_col="symbol")
            if not parity_detail.is_empty():
                await self.buffered_writer.append_derived(parity_detail, dataset="parity_detail", partition_col="symbol")
            if not calibration.is_empty() and self._should_flush_diagnostics(symbol, flush_calibration_diagnostics):
                await self.buffered_writer.append_derived(calibration, dataset="diagnostics", partition_col="symbol")
                self._last_diag_flush_ts[symbol] = time.monotonic()
            if not quote_quality_points.is_empty():
                await self.buffered_writer.append_derived(
                    quote_quality_points,
                    dataset="quote_quality_points",
                    partition_col="symbol",
                )
            if not surface_points.is_empty():
                await self.buffered_writer.append_derived(surface_points, dataset="surface_points", partition_col="symbol")
            if not surface_diagnostics.is_empty():
                await self.buffered_writer.append_derived(
                    surface_diagnostics,
                    dataset="surface_diagnostics",
                    partition_col="symbol",
                )
            if not focus_expiry_summary.is_empty():
                await self.buffered_writer.append_derived(
                    focus_expiry_summary,
                    dataset="focus_expiry_summary",
                    partition_col="symbol",
                )
            if not dealer_exposure_points.is_empty():
                await self.buffered_writer.append_derived(
                    dealer_exposure_points,
                    dataset="dealer_exposure_points",
                    partition_col="symbol",
                )
            if not flow_proxy_points.is_empty():
                await self.buffered_writer.append_derived(
                    flow_proxy_points,
                    dataset="flow_proxy_points",
                    partition_col="symbol",
                )
            if not scanner_levels.is_empty():
                await self.buffered_writer.append_derived(
                    scanner_levels,
                    dataset="scanner_levels",
                    partition_col="symbol",
                )
            catalog = _snapshot_catalog_row(
                batch_id=batch_id,
                symbol=symbol,
                asof_ts=asof_ts,
                trading_date=trading_date,
                snapshot_kind=snapshot_kind,
                source_mode=source_mode,
                is_final_for_day=is_final_for_day,
                parent_batch_id=parent_batch_id,
                raw_rows=routed.height,
                greeks_rows=greeks.height,
                diagnostics_rows=calibration.height,
                updated_at_utc=payload.updated_at_utc,
            )
            await self.buffered_writer.append_derived(catalog, dataset="snapshot_catalog", partition_col="symbol")
        latency["persist_ms"] = (time.perf_counter() - t0) * 1000.0
        latency["total_ms"] = (time.perf_counter() - batch_start) * 1000.0
        current_snapshot = self.cache.get_snapshot_nowait(symbol)
        runtime_metrics = make_runtime_metrics_row(
            symbol=symbol,
            asof_ts=asof_ts,
            batch_id=batch_id,
            version=version,
            snapshot_kind=snapshot_kind,
            source_mode=source_mode,
            trading_date=trading_date,
            runtime_mode=self.config.runtime_mode,
            latency_ms=latency,
            raw_rows=routed.height,
            greeks_rows=greeks.height,
            surface_rows=surface_points.height,
            surface_summary_rows=surface_diagnostics.height,
            diagnostics_rows=calibration.height,
            memory_bytes=current_snapshot.memory_bytes if current_snapshot is not None else {},
            drop_counters=current_snapshot.drop_counters if current_snapshot is not None else {},
        )
        self.cache.append_history(symbol, "runtime_metrics", runtime_metrics)
        if self.derived_store is not None:
            await self.buffered_writer.append_derived(runtime_metrics, dataset="runtime_metrics", partition_col="symbol")

        for row in parity_summary.to_dicts():
            logger.info(
                "parity_winner symbol=%s expiration=%s winner=%s bjerksund_err=%.6f luba_err=%.6f pairs=%s",
                symbol,
                row["expiration"],
                row["winner_model"],
                row["bjerksund_error"],
                row["luba_error"],
                row["pairs"],
            )
        logger.info(
            "pipeline_batch_ok symbol=%s version=%s snapshot_kind=%s latency_ms=%.2f",
            symbol,
            version,
            snapshot_kind,
            latency["total_ms"],
        )

        if ssvi_error is not None:
            logger.error(ssvi_error)
            status["failure_reason"] = ssvi_error
            return
    async def _compute_and_store_diagnostics(
        self,
        symbol: str,
        frame: pl.DataFrame,
        flush_calibration_diagnostics: bool = False,
    ) -> None:
        dispatch = build_dispatch_summary(frame)
        parity_summary, parity_detail, parity_solver_diag = evaluate_parity_diagnostics(
            frame,
            rate=self.config.parity_rate,
            dividend=self.config.parity_dividend,
            eep_mode=self.config.parity_eep_mode,
            max_pairs=self.config.parity_max_pairs,
            tree_steps=self.config.parity_tree_steps,
            luba_method=self.config.parity_luba_method,
            rim_nodes=self.config.parity_rim_nodes,
            return_solver_diagnostics=True,
        )
        asof_ts = frame["asof_ts"][0] if "asof_ts" in frame.columns else _now_utc()
        diag_frames: list[pl.DataFrame] = []
        if not parity_solver_diag.is_empty():
            for row in parity_solver_diag.to_dicts():
                diag_frames.append(
                    make_calibration_diagnostic_row(
                        symbol=symbol,
                        asof_ts=asof_ts,
                        expiration=row.get("expiration"),
                        model_id=str(row.get("model_id", "luba_rim")),
                        backend_used="python",
                        runtime_mode=self.config.runtime_mode,
                        converged=bool(row.get("converged", False)),
                        iterations=int(row.get("iterations", 0)),
                        sse_final=float(row.get("sse_final", float("inf"))),
                        durrleman_pass=bool(row.get("durrleman_pass", True)),
                        failure_reason="",
                        params=row.get("params") if isinstance(row.get("params"), dict) else None,
                    )
                )
        cal = pl.concat(diag_frames, how="vertical") if diag_frames else pl.DataFrame()
        await self.cache._publish_partial(  # noqa: SLF001
            symbol,
            dispatch=dispatch,
            parity=parity_summary,
            parity_detail=parity_detail,
            calibration_diag=cal if not cal.is_empty() else self.cache.get_calibration_diagnostics_nowait(symbol),
        )
        if flush_calibration_diagnostics and not cal.is_empty() and self.derived_store is not None:
            await self.buffered_writer.append_derived(cal, dataset="diagnostics", partition_col="symbol")

    def _should_flush_diagnostics(self, symbol: str, force: bool) -> bool:
        if force:
            return True
        last = self._last_diag_flush_ts.get(symbol, 0.0)
        return (time.monotonic() - last) >= self.config.diag_flush_interval_sec

    async def _run_memory_monitor(self, stop_event: asyncio.Event) -> None:
        process = psutil.Process()
        soft_bytes = self.config.memory_soft_limit_mb * 1024 * 1024
        hard_bytes = self.config.memory_hard_limit_mb * 1024 * 1024
        while not stop_event.is_set():
            await asyncio.sleep(self.config.memory_check_interval_sec)
            state_bytes = self.cache.estimate_total_bytes()
            rss_bytes = int(process.memory_info().rss)
            over_soft = state_bytes >= soft_bytes or rss_bytes >= soft_bytes
            over_hard = state_bytes >= hard_bytes or rss_bytes >= hard_bytes
            now = time.monotonic()

            if over_hard:
                self._trim_all_symbols(reason="hard", state_bytes=state_bytes, rss_bytes=rss_bytes)
                gc.collect()
                self._soft_exceed_count = 0
                self._last_trim_ts = now
                continue

            if over_soft:
                self._soft_exceed_count += 1
            else:
                self._soft_exceed_count = 0

            if self._soft_exceed_count < 3:
                continue
            if now - self._last_trim_ts < self.config.memory_trim_cooldown_sec:
                continue

            self._trim_all_symbols(reason="soft", state_bytes=state_bytes, rss_bytes=rss_bytes)
            gc.collect()
            self._last_trim_ts = now
            self._soft_exceed_count = 0

    def _trim_all_symbols(self, *, reason: str, state_bytes: int, rss_bytes: int) -> None:
        stats = self.cache.stats_frame()
        if stats.is_empty() or "symbol" not in stats.columns:
            return
        trims: dict[str, dict[str, int]] = {}
        for symbol in stats["symbol"].to_list():
            out = self.cache.trim_to_budget(str(symbol))
            if out:
                trims[str(symbol)] = out
        logger.warning(
            "memory_trim reason=%s state_bytes=%s rss_bytes=%s trim=%s",
            reason,
            state_bytes,
            rss_bytes,
            trims,
        )


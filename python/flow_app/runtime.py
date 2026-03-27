from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from flow_core.config import PipelineConfig, ProviderMap, load_yaml
from flow_core.ingestion.providers import YFinanceAdapter
from flow_core.orchestration import InMemoryQuoteCache
from flow_core.orchestration.pipeline import QuantPipelineService
from flow_core.orchestration.refresh_service import UIRefreshService
from flow_core.orchestration.run_lock import StreamRunLock
from flow_core.orchestration.state_store import LiveStateStore
from flow_core.quant.market_inputs import HybridDividendSource, TBillRateCurve
from flow_core.storage import ParquetStore

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER_CONFIG = "configs/providers/yfinance.yaml"
DEFAULT_PIPELINE_CONFIG = "configs/pipeline/default.yaml"
TickerSearchCallback = Callable[[str], list[dict[str, str]]]
ExpirationLookupCallback = Callable[[str], list[str]]


class AppMode(str, Enum):
    UI_REVIEW = "ui_review"
    UI_LIVE = "ui_live"
    HEADLESS_LIVE = "headless_live"
    SNAPSHOT_ONCE = "snapshot_once"


@dataclass(slots=True)
class LaunchConfig:
    mode: AppMode = AppMode.UI_REVIEW
    ticker: str = "SPY"
    expiration: str | None = None
    refresh_ms: int = 0
    allow_shared: bool = False
    provider_config: str = DEFAULT_PROVIDER_CONFIG
    pipeline_config: str = DEFAULT_PIPELINE_CONFIG

    def normalized(self) -> "LaunchConfig":
        ticker = self.ticker.strip().upper() or "SPY"
        expiration = (self.expiration or "").strip() or None
        provider_config = self.provider_config.strip() or DEFAULT_PROVIDER_CONFIG
        pipeline_config = self.pipeline_config.strip() or DEFAULT_PIPELINE_CONFIG
        refresh_ms = max(int(self.refresh_ms), 0)
        mode = self.mode if isinstance(self.mode, AppMode) else AppMode(str(self.mode))
        return LaunchConfig(
            mode=mode,
            ticker=ticker,
            expiration=expiration,
            refresh_ms=refresh_ms,
            allow_shared=bool(self.allow_shared),
            provider_config=provider_config,
            pipeline_config=pipeline_config,
        )

    def to_dict(self) -> dict[str, Any]:
        normalized = self.normalized()
        return {
            "mode": normalized.mode.value,
            "ticker": normalized.ticker,
            "expiration": normalized.expiration or "",
            "refresh_ms": normalized.refresh_ms,
            "allow_shared": normalized.allow_shared,
            "provider_config": normalized.provider_config,
            "pipeline_config": normalized.pipeline_config,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "LaunchConfig":
        data = dict(payload or {})
        mode_raw = data.get("mode", AppMode.UI_REVIEW.value)
        try:
            mode = AppMode(str(mode_raw))
        except ValueError:
            mode = AppMode.UI_REVIEW
        return cls(
            mode=mode,
            ticker=str(data.get("ticker", "SPY")),
            expiration=str(data.get("expiration", "") or "") or None,
            refresh_ms=int(data.get("refresh_ms", 0) or 0),
            allow_shared=bool(data.get("allow_shared", False)),
            provider_config=str(data.get("provider_config", DEFAULT_PROVIDER_CONFIG)),
            pipeline_config=str(data.get("pipeline_config", DEFAULT_PIPELINE_CONFIG)),
        ).normalized()


@dataclass(slots=True)
class LiveSelectionState:
    expiration: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def get_expiration(self) -> str | None:
        with self._lock:
            return self.expiration

    def set_expiration(self, expiration: str | None) -> None:
        with self._lock:
            self.expiration = (expiration or "").strip() or None


@dataclass(slots=True)
class LiveRuntimeStatus:
    state: str = "idle"
    message: str = "live polling not started"
    symbol: str = ""
    expiration: str = "auto"
    fetch_scope: str = "n/a"
    cadence_mode: str = "n/a"
    cadence_hot_seconds: int = 0
    cadence_full_snapshot_seconds: int = 0
    rows: int = 0
    latency_ms: float = 0.0
    error_type: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def update(self, payload: dict[str, object]) -> None:
        with self._lock:
            if "state" in payload:
                self.state = str(payload["state"])
            if "message" in payload:
                self.message = str(payload["message"])
            if "symbol" in payload:
                self.symbol = str(payload["symbol"])
            if "expiration" in payload:
                self.expiration = str(payload["expiration"])
            if "fetch_scope" in payload:
                self.fetch_scope = str(payload["fetch_scope"])
            if "cadence_mode" in payload:
                self.cadence_mode = str(payload["cadence_mode"])
            if "cadence_hot_seconds" in payload:
                self.cadence_hot_seconds = int(payload["cadence_hot_seconds"])
            if "cadence_full_snapshot_seconds" in payload:
                self.cadence_full_snapshot_seconds = int(payload["cadence_full_snapshot_seconds"])
            if "rows" in payload:
                self.rows = int(payload["rows"])
            if "latency_ms" in payload:
                self.latency_ms = float(payload["latency_ms"])
            if "error_type" in payload:
                self.error_type = str(payload["error_type"])

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self.state,
                "message": self.message,
                "symbol": self.symbol,
                "expiration": self.expiration,
                "fetch_scope": self.fetch_scope,
                "cadence_mode": self.cadence_mode,
                "cadence_hot_seconds": self.cadence_hot_seconds,
                "cadence_full_snapshot_seconds": self.cadence_full_snapshot_seconds,
                "rows": self.rows,
                "latency_ms": self.latency_ms,
                "error_type": self.error_type,
            }


def build_lookup_callbacks() -> tuple[TickerSearchCallback, ExpirationLookupCallback]:
    adapter = YFinanceAdapter()
    return (
        lambda query: adapter.search_symbols_sync(query, max_results=5),
        adapter.fetch_available_expirations_sync,
    )


def session_state_path() -> Path:
    local_appdata = Path.home()
    if value := os.environ.get("LOCALAPPDATA"):
        local_appdata = Path(value)
    return local_appdata / "quant-pipeline-mvp" / "launch_config.json"


def load_launch_config() -> LaunchConfig:
    path = session_state_path()
    if not path.exists():
        return LaunchConfig()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("launch_config_load_failed path=%s", path, exc_info=True)
        return LaunchConfig()
    return LaunchConfig.from_dict(payload)


def save_launch_config(config: LaunchConfig) -> Path:
    path = session_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
    return path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified app launcher for UI, live, and snapshot modes.")
    parser.add_argument("--mode", choices=[mode.value for mode in AppMode], default=None)
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--expiration", default=None)
    parser.add_argument("--refresh-ms", type=int, default=None)
    parser.add_argument("--provider-config", default=None)
    parser.add_argument("--pipeline-config", default=None)
    parser.add_argument("--allow-shared", action="store_true")
    parser.add_argument(
        "--no-launcher",
        action="store_true",
        help="Use CLI or persisted settings directly instead of opening the startup launcher.",
    )
    return parser


def merge_cli_overrides(base: LaunchConfig, args: argparse.Namespace) -> LaunchConfig:
    merged = base.to_dict()
    if args.mode is not None:
        merged["mode"] = args.mode
    if args.ticker is not None:
        merged["ticker"] = args.ticker
    if args.expiration is not None:
        merged["expiration"] = args.expiration
    if args.refresh_ms is not None:
        merged["refresh_ms"] = args.refresh_ms
    if args.provider_config is not None:
        merged["provider_config"] = args.provider_config
    if args.pipeline_config is not None:
        merged["pipeline_config"] = args.pipeline_config
    if args.allow_shared:
        merged["allow_shared"] = True
    return LaunchConfig.from_dict(merged)


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def build_state_store(config: PipelineConfig) -> LiveStateStore:
    return LiveStateStore(
        dataset_budgets_mb={
            "raw": config.state_budget_raw_mb,
            "greeks": config.state_budget_greeks_mb,
            "ssvi": config.state_budget_ssvi_mb,
            "dispatch": config.state_budget_dispatch_mb,
            "parity": config.state_budget_parity_mb,
            "parity_detail": config.state_budget_parity_detail_mb,
            "diagnostics": config.state_budget_diagnostics_mb,
            "overlay": config.state_budget_overlay_mb,
        },
        dataset_row_caps={
            "raw": config.state_max_rows_raw,
            "greeks": config.state_max_rows_greeks,
            "ssvi": config.state_max_rows_ssvi,
            "diagnostics": config.state_max_rows_diagnostics,
        },
        max_symbols=config.state_max_symbols,
    )


def build_pipeline_service(
    cache: InMemoryQuoteCache,
    config: PipelineConfig,
    provider_map: ProviderMap,
) -> QuantPipelineService:
    rate_curve = (
        TBillRateCurve(refresh_seconds=config.rate_curve_refresh_seconds) if config.use_yfinance_rate_curve else None
    )
    dividend_source = (
        HybridDividendSource(
            projection_horizon_years=config.dividend_projection_horizon_years,
            lookback_events=config.dividend_lookback_events,
        )
        if config.use_projected_dividends
        else None
    )
    return QuantPipelineService(
        adapter=YFinanceAdapter(),
        provider_map=provider_map,
        config=config,
        cache=cache,
        parquet_store=ParquetStore(config.parquet_root),
        derived_store=ParquetStore(config.derived_parquet_root),
        rate_curve=rate_curve,
        dividend_source=dividend_source,
    )


def should_start_live_session(config: PipelineConfig) -> bool:
    now_et = datetime.now(ZoneInfo(config.snapshot_timezone))
    return now_et.strftime("%H:%M") < config.market_close_freeze_time


def load_runtime_settings(launch: LaunchConfig) -> tuple[ProviderMap, PipelineConfig]:
    provider_map = load_yaml(launch.provider_config, ProviderMap)
    pipeline_config = load_yaml(launch.pipeline_config, PipelineConfig)
    return provider_map, pipeline_config


def build_runtime_summary(
    launch: LaunchConfig,
    config: PipelineConfig,
    *,
    refresh_ms: int,
) -> dict[str, object]:
    expiration_summary = launch.expiration or "auto"
    if launch.mode is AppMode.SNAPSHOT_ONCE:
        expiration_summary = "full_surface (all expiries)"
    return {
        "app_mode": launch.mode.value,
        "ticker": launch.ticker,
        "expiration": expiration_summary,
        "refresh_ms": refresh_ms,
        "runtime_mode": config.runtime_mode,
        "ssvi_backend": config.ssvi_backend,
        "fdm_backend": config.fdm_backend,
        "live_poll_seconds": config.live_poll_seconds,
        "live_focus_labels": ",".join(str(label) for label in config.live_focus_labels),
        "live_hot_poll_seconds": config.live_hot_poll_seconds,
        "live_full_snapshot_poll_seconds": config.live_full_snapshot_poll_seconds,
        "stream_lock_enforced": config.stream_lock_enforced,
        "provider_config": launch.provider_config,
        "pipeline_config": launch.pipeline_config,
        "session_state_path": str(session_state_path()),
    }


def _save_session_payload(payload: dict[str, Any]) -> str:
    path = save_launch_config(LaunchConfig.from_dict(payload))
    return f"Saved next-launch session to {path}"


def _build_cache(config: PipelineConfig) -> InMemoryQuoteCache:
    return InMemoryQuoteCache(_state_store=build_state_store(config))


async def _run_headless_live(launch: LaunchConfig) -> int:
    provider_map, config = load_runtime_settings(launch)
    now_et = datetime.now(ZoneInfo(config.snapshot_timezone))
    if now_et.strftime("%H:%M") >= config.market_close_freeze_time:
        logger.info(
            "headless_live skipped because current time is after market_close_freeze_time=%s",
            config.market_close_freeze_time,
        )
        return 0

    lock: StreamRunLock | None = None
    if config.stream_lock_enforced and not launch.allow_shared:
        lock = StreamRunLock(symbol=launch.ticker, mode=launch.mode.value)
        lock.acquire()

    try:
        service = build_pipeline_service(_build_cache(config), config, provider_map)
        await service.run_live(symbol=launch.ticker, expiration=launch.expiration)
    finally:
        if lock is not None:
            lock.release()
    return 0


async def _run_snapshot_once(launch: LaunchConfig) -> int:
    provider_map, config = load_runtime_settings(launch)
    service = build_pipeline_service(_build_cache(config), config, provider_map)
    logger.info(
        "snapshot_once_full_surface symbol=%s requested_expiration=%s",
        launch.ticker,
        launch.expiration or "auto",
    )
    frame = await service.capture_snapshot(launch.ticker)
    print(f"snapshot rows={frame.height}")
    return 0


def _start_ui_live_worker(
    service: QuantPipelineService,
    *,
    ticker: str,
    expiration: str | None,
    expiration_resolver: Callable[[], str | None] | None,
    status_callback: Callable[[dict[str, object]], None] | None,
    stop_event: threading.Event,
) -> threading.Thread:
    def _runner() -> None:
        asyncio.run(
            service.run_live(
                symbol=ticker,
                expiration=expiration,
                expiration_resolver=expiration_resolver,
                status_callback=status_callback,
                stop_event=stop_event,
            )
        )

    thread = threading.Thread(target=_runner, name="live-ingestion", daemon=True)
    thread.start()
    return thread


def _run_ui_mode(launch: LaunchConfig, *, app: Any | None = None) -> int:
    from flow_ui.main import run_ui
    from flow_ui.state_bridge import UIStateBridge

    provider_map, config = load_runtime_settings(launch)
    symbol_search_callback, expiration_lookup_callback = build_lookup_callbacks()
    cache = _build_cache(config)
    bridge = UIStateBridge(max_pending_per_symbol=config.ui_max_pending_per_symbol)
    refresh_pipeline = build_pipeline_service(cache, config, provider_map)
    refresh_service = UIRefreshService(
        pipeline=refresh_pipeline,
        adapter=refresh_pipeline.adapter,
        provider_map=provider_map,
        cache=cache,
        config=config,
    )
    bootstrap_result = refresh_service.hydrate_latest_snapshot(launch.ticker)
    bootstrap_msg = bootstrap_result.message
    logger.info("ui_bootstrap %s", bootstrap_msg)

    stop_event = threading.Event()
    worker_thread: threading.Thread | None = None
    lock: StreamRunLock | None = None
    live_selection = LiveSelectionState(launch.expiration)
    live_status = LiveRuntimeStatus(symbol=launch.ticker, expiration=launch.expiration or "auto")
    start_live = False
    if launch.mode is AppMode.UI_LIVE:
        start_live = should_start_live_session(config)
        if start_live and config.stream_lock_enforced and not launch.allow_shared:
            lock = StreamRunLock(symbol=launch.ticker, mode=launch.mode.value)
            lock.acquire()
        if start_live:
            live_pipeline = build_pipeline_service(cache, config, provider_map)
            live_status.update({"state": "starting", "message": "live polling thread starting", "symbol": launch.ticker})
            worker_thread = _start_ui_live_worker(
                live_pipeline,
                ticker=launch.ticker,
                expiration=launch.expiration,
                expiration_resolver=live_selection.get_expiration,
                status_callback=live_status.update,
                stop_event=stop_event,
            )
        else:
            live_status.update(
                {
                    "state": "skipped",
                    "message": f"live polling skipped because current time is after {config.market_close_freeze_time}",
                    "symbol": launch.ticker,
                }
            )
            logger.info(
                "ui_live_start_skipped current_time is after market_close_freeze_time=%s",
                config.market_close_freeze_time,
            )

    refresh_ms = launch.refresh_ms if launch.refresh_ms > 0 else config.ui_apply_interval_ms
    session_payload = launch.to_dict()
    session_payload["refresh_ms"] = refresh_ms
    runtime_summary = build_runtime_summary(launch, config, refresh_ms=refresh_ms)
    try:
        return run_ui(
            app=app,
            cache=cache,
            refresh_ms=refresh_ms,
            symbol=launch.ticker,
            bridge=bridge,
            default_space_mode=config.ui_overlay_default_mode,
            dual_mode_enabled=config.ui_overlay_dual_mode_enabled,
            ui_apply_p95_limit_ms=config.ui_apply_p95_limit_ms,
            ui_auto_degrade=config.ui_auto_degrade_enabled,
            refresh_callback=lambda: refresh_service.refresh_for_ui(launch.ticker).message,
            history_callback=refresh_service.load_chart_history,
            history_error_callback=refresh_service.get_dataset_read_error,
            snapshot_timezone=config.snapshot_timezone,
            market_close_freeze_time=config.market_close_freeze_time,
            final_prices_refresh_time=config.final_prices_refresh_time,
            oi_refresh_time=config.oi_refresh_time,
            session_config=session_payload,
            runtime_summary=runtime_summary,
            session_save_callback=_save_session_payload,
            bootstrap_message=bootstrap_msg,
            symbol_search_callback=symbol_search_callback,
            expiration_lookup_callback=expiration_lookup_callback,
            batch_list_callback=refresh_service.list_available_batches,
            batch_select_callback=refresh_service.hydrate_selected_snapshot,
            pull_snapshot_callback=refresh_service.capture_full_snapshot_for_ui,
            live_expiration=live_selection.get_expiration(),
            live_expiration_setter=live_selection.set_expiration if launch.mode is AppMode.UI_LIVE else None,
            live_expiration_enabled=start_live,
            live_runtime_status_callback=live_status.snapshot if launch.mode is AppMode.UI_LIVE else None,
        )
    finally:
        stop_event.set()
        if worker_thread is not None:
            worker_thread.join(timeout=5.0)
        if lock is not None:
            lock.release()


def run_launch_config(launch: LaunchConfig, *, app: Any | None = None) -> int:
    launch = launch.normalized()
    save_launch_config(launch)
    if launch.mode in {AppMode.UI_REVIEW, AppMode.UI_LIVE}:
        return _run_ui_mode(launch, app=app)
    if launch.mode is AppMode.HEADLESS_LIVE:
        return asyncio.run(_run_headless_live(launch))
    if launch.mode is AppMode.SNAPSHOT_ONCE:
        return asyncio.run(_run_snapshot_once(launch))
    raise ValueError(f"Unsupported launch mode: {launch.mode}")


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    args = build_arg_parser().parse_args(argv)
    initial = merge_cli_overrides(load_launch_config(), args)
    if args.mode is None and not args.no_launcher:
        from PySide6.QtWidgets import QApplication

        from flow_app.launcher import prompt_for_launch_config

        symbol_search_callback, expiration_lookup_callback = build_lookup_callbacks()
        app = QApplication.instance() or QApplication(sys.argv)
        selected = prompt_for_launch_config(
            initial,
            app=app,
            symbol_search_callback=symbol_search_callback,
            expiration_lookup_callback=expiration_lookup_callback,
        )
        if selected is None:
            return 0
        return run_launch_config(selected, app=app)
    return run_launch_config(initial)





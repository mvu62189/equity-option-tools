from __future__ import annotations

import argparse
import asyncio
import logging
import threading
from datetime import datetime
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
from flow_ui.main import run_ui
from flow_ui.state_bridge import UIStateBridge


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Qt UI (optionally with live ingestion)")
    parser.add_argument("--ticker", default="SPY")
    parser.add_argument("--refresh-ms", type=int, default=0)
    parser.add_argument("--with-live", action="store_true")
    parser.add_argument("--expiration", default=None)
    parser.add_argument("--provider-config", default="configs/providers/yfinance.yaml")
    parser.add_argument("--pipeline-config", default="configs/pipeline/default.yaml")
    parser.add_argument("--allow-shared", action="store_true")
    return parser.parse_args()


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
    service = QuantPipelineService(
        adapter=YFinanceAdapter(),
        provider_map=provider_map,
        config=config,
        cache=cache,
        parquet_store=ParquetStore(config.parquet_root),
        derived_store=ParquetStore(config.derived_parquet_root),
        rate_curve=rate_curve,
        dividend_source=dividend_source,
    )
    return service


def start_live_worker(
    service: QuantPipelineService,
    args: argparse.Namespace,
    stop_event: threading.Event,
) -> threading.Thread:

    def _runner() -> None:
        asyncio.run(service.run_live(symbol=args.ticker, expiration=args.expiration, stop_event=stop_event))

    thread = threading.Thread(target=_runner, name="live-ingestion", daemon=True)
    thread.start()
    return thread


def should_start_live_session(config: PipelineConfig) -> bool:
    now_et = datetime.now(ZoneInfo(config.snapshot_timezone))
    return now_et.strftime("%H:%M") < config.market_close_freeze_time


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    provider_map = load_yaml(args.provider_config, ProviderMap)
    config = load_yaml(args.pipeline_config, PipelineConfig)
    state_store = LiveStateStore(
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
    cache = InMemoryQuoteCache(_state_store=state_store)
    bridge = UIStateBridge(max_pending_per_symbol=config.ui_max_pending_per_symbol)
    refresh_pipeline = build_pipeline_service(cache, config, provider_map)
    refresh_service = UIRefreshService(
        pipeline=refresh_pipeline,
        adapter=refresh_pipeline.adapter,
        provider_map=provider_map,
        cache=cache,
        config=config,
    )
    bootstrap_msg = refresh_service.hydrate_latest_snapshot(args.ticker).message
    logging.getLogger(__name__).info("ui_bootstrap %s", bootstrap_msg)
    stop_event = threading.Event()
    worker_thread: threading.Thread | None = None
    lock: StreamRunLock | None = None
    start_live = args.with_live and should_start_live_session(config)
    if start_live and config.stream_lock_enforced and not args.allow_shared:
        lock = StreamRunLock(symbol=args.ticker, mode="run_ui_live")
        lock.acquire()
    if start_live:
        live_pipeline = build_pipeline_service(cache, config, provider_map)
        worker_thread = start_live_worker(live_pipeline, args, stop_event)
    elif args.with_live:
        logging.getLogger(__name__).info(
            "ui_live_start_skipped current_time is after market_close_freeze_time=%s",
            config.market_close_freeze_time,
        )

    refresh_ms = args.refresh_ms if args.refresh_ms > 0 else config.ui_apply_interval_ms
    try:
        run_ui(
            cache=cache,
            refresh_ms=refresh_ms,
            symbol=args.ticker,
            bridge=bridge,
            default_space_mode=config.ui_overlay_default_mode,
            dual_mode_enabled=config.ui_overlay_dual_mode_enabled,
            ui_apply_p95_limit_ms=config.ui_apply_p95_limit_ms,
            ui_auto_degrade=config.ui_auto_degrade_enabled,
            refresh_callback=lambda: refresh_service.refresh_for_ui(args.ticker).message,
            history_callback=refresh_service.load_chart_history,
            snapshot_timezone=config.snapshot_timezone,
            market_close_freeze_time=config.market_close_freeze_time,
            final_prices_refresh_time=config.final_prices_refresh_time,
            oi_refresh_time=config.oi_refresh_time,
        )
    finally:
        stop_event.set()
        if worker_thread is not None:
            worker_thread.join(timeout=5.0)
        if lock is not None:
            lock.release()

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from .runtime import AppMode, LaunchConfig, run_launch_config

logger = logging.getLogger(__name__)


def _deprecated(script_name: str, replacement: str) -> None:
    logger.warning("%s is deprecated; use `%s` instead.", script_name, replacement)


def run_ui_legacy(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Qt UI (optionally with live ingestion)")
    parser.add_argument("--ticker", default="SPY")
    parser.add_argument("--refresh-ms", type=int, default=0)
    parser.add_argument("--with-live", action="store_true")
    parser.add_argument("--expiration", default=None)
    parser.add_argument("--provider-config", default="configs/providers/yfinance.yaml")
    parser.add_argument("--pipeline-config", default="configs/pipeline/default.yaml")
    parser.add_argument("--allow-shared", action="store_true")
    args = parser.parse_args(argv)
    mode = AppMode.UI_LIVE if args.with_live else AppMode.UI_REVIEW
    _deprecated("scripts/run_ui.py", f"python -m flow_app --mode {mode.value}")
    return run_launch_config(
        LaunchConfig(
            mode=mode,
            ticker=args.ticker,
            expiration=args.expiration,
            refresh_ms=args.refresh_ms,
            allow_shared=args.allow_shared,
            provider_config=args.provider_config,
            pipeline_config=args.pipeline_config,
        ).normalized()
    )


def run_headless_live_legacy(argv: Sequence[str] | None = None, *, script_name: str = "scripts/run_live.py") -> int:
    parser = argparse.ArgumentParser(description="Run headless live ingestion pipeline")
    parser.add_argument("--ticker", default="SPY")
    parser.add_argument("--expiration", default=None)
    parser.add_argument("--provider-config", default="configs/providers/yfinance.yaml")
    parser.add_argument("--pipeline-config", default="configs/pipeline/default.yaml")
    parser.add_argument("--allow-shared", action="store_true")
    args = parser.parse_args(argv)
    _deprecated(script_name, "python -m flow_app --mode headless_live")
    return run_launch_config(
        LaunchConfig(
            mode=AppMode.HEADLESS_LIVE,
            ticker=args.ticker,
            expiration=args.expiration,
            allow_shared=args.allow_shared,
            provider_config=args.provider_config,
            pipeline_config=args.pipeline_config,
        ).normalized()
    )


def run_snapshot_legacy(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture full option snapshot")
    parser.add_argument("--ticker", default="SPY")
    parser.add_argument("--provider-config", default="configs/providers/yfinance.yaml")
    parser.add_argument("--pipeline-config", default="configs/pipeline/default.yaml")
    args = parser.parse_args(argv)
    _deprecated("scripts/run_snapshot.py", "python -m flow_app --mode snapshot_once")
    return run_launch_config(
        LaunchConfig(
            mode=AppMode.SNAPSHOT_ONCE,
            ticker=args.ticker,
            provider_config=args.provider_config,
            pipeline_config=args.pipeline_config,
        ).normalized()
    )


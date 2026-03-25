from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import polars as pl

from flow_core.config.models import PipelineConfig, ProviderMap
from flow_core.ingestion.mapper import map_provider_records
from flow_core.ingestion.providers.base import ProviderAdapter

logger = logging.getLogger(__name__)


class LiveIngestionWorker:
    def __init__(
        self,
        adapter: ProviderAdapter,
        provider_map: ProviderMap,
        config: PipelineConfig,
        on_batch: Callable[[pl.DataFrame], Awaitable[None]],
        status_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._adapter = adapter
        self._provider_map = provider_map
        self._config = config
        self._on_batch = on_batch
        self._status_callback = status_callback
        self._running = False
        self._recent_fetch_ms: deque[float] = deque(maxlen=20)
        self._error_streak = 0
        self._supports_full_snapshot = callable(getattr(adapter, "fetch_full_snapshot", None))

    async def run(
        self,
        symbol: str,
        expiration: str | None = None,
        expiration_resolver: Callable[[], str | None] | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._running = True
        self._emit_status({"state": "starting", "symbol": symbol, "message": "live polling started"})
        base_hot_seconds = max(int(self._config.live_hot_poll_seconds), 1)
        base_full_seconds = max(int(self._config.live_full_snapshot_poll_seconds), base_hot_seconds)
        next_full_snapshot = time.monotonic() + base_full_seconds
        while self._running and (stop_event is None or not stop_event.is_set()):
            start = time.perf_counter()
            resolved_expiration = expiration
            hot_expiration = expiration
            current_hot_seconds, current_full_seconds, cadence_mode = self._cadence_profile(
                base_hot_seconds=base_hot_seconds,
                base_full_seconds=base_full_seconds,
            )
            fetch_scope = "configured_expiration"
            try:
                resolved_expiration = expiration_resolver() if expiration_resolver is not None else expiration
                hot_expiration = resolved_expiration
                if not hot_expiration:
                    focus_expiries = await self._resolve_focus_expiries(symbol)
                    if focus_expiries:
                        hot_expiration = ",".join(focus_expiries)
                        fetch_scope = "focused_short"
                    else:
                        hot_expiration = self._config.live_expiry_scope
                        fetch_scope = str(self._config.live_expiry_scope)

                do_full_snapshot = self._supports_full_snapshot and time.monotonic() >= next_full_snapshot
                if do_full_snapshot:
                    raw = await self._fetch_full_snapshot_with_retry(symbol)
                    fetch_scope = "full_surface"
                    next_full_snapshot = time.monotonic() + current_full_seconds
                else:
                    raw = await self._fetch_with_retry(symbol, hot_expiration)
                mapped = map_provider_records(raw, self._provider_map, underlying_symbol=symbol)
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                self._recent_fetch_ms.append(elapsed_ms)
                self._error_streak = 0
                if mapped.is_empty():
                    self._emit_status(
                        {
                            "state": "empty",
                            "symbol": symbol,
                            "expiration": hot_expiration or "auto",
                            "fetch_scope": fetch_scope,
                            "cadence_mode": cadence_mode,
                            "cadence_hot_seconds": current_hot_seconds,
                            "cadence_full_snapshot_seconds": current_full_seconds,
                            "message": "provider returned no option rows for the current live scope",
                        }
                    )
                    logger.warning("live_batch_empty symbol=%s expiration=%s scope=%s", symbol, hot_expiration or "auto", fetch_scope)
                else:
                    await self._on_batch(mapped)
                    self._emit_status(
                        {
                            "state": "ok",
                            "symbol": symbol,
                            "expiration": hot_expiration or "auto",
                            "fetch_scope": fetch_scope,
                            "cadence_mode": cadence_mode,
                            "cadence_hot_seconds": current_hot_seconds,
                            "cadence_full_snapshot_seconds": current_full_seconds,
                            "rows": mapped.height,
                            "latency_ms": elapsed_ms,
                            "message": "live batch received",
                        }
                    )
                    logger.info(
                        "live_batch_ok symbol=%s rows=%s latency_ms=%.2f scope=%s cadence_mode=%s",
                        symbol,
                        mapped.height,
                        elapsed_ms,
                        fetch_scope,
                        cadence_mode,
                    )
            except Exception as exc:  # pragma: no cover
                self._error_streak += 1
                self._emit_status(
                    {
                        "state": "error",
                        "symbol": symbol,
                        "expiration": hot_expiration or resolved_expiration or expiration or "auto",
                        "fetch_scope": fetch_scope,
                        "cadence_mode": cadence_mode,
                        "cadence_hot_seconds": current_hot_seconds,
                        "cadence_full_snapshot_seconds": current_full_seconds,
                        "message": str(exc),
                        "error_type": exc.__class__.__name__,
                    }
                )
                logger.exception("live_batch_failed symbol=%s err=%s", symbol, exc)
            if stop_event is not None and stop_event.is_set():
                break
            await asyncio.sleep(current_hot_seconds)
        self._emit_status({"state": "stopped", "symbol": symbol, "message": "live polling stopped"})

    def stop(self) -> None:
        self._running = False

    async def _fetch_with_retry(self, symbol: str, expiration: str | None) -> pl.DataFrame:
        retries = self._config.max_retries
        backoff = self._config.retry_backoff_seconds
        attempt = 0
        while True:
            try:
                return await self._adapter.fetch_option_chain(symbol=symbol, expiration=expiration)
            except Exception:
                attempt += 1
                if attempt > retries:
                    raise
                await asyncio.sleep(backoff * attempt)

    async def _fetch_full_snapshot_with_retry(self, symbol: str) -> pl.DataFrame:
        retries = self._config.max_retries
        backoff = self._config.retry_backoff_seconds
        attempt = 0
        while True:
            try:
                return await self._adapter.fetch_full_snapshot(symbol=symbol)
            except Exception:
                attempt += 1
                if attempt > retries:
                    raise
                await asyncio.sleep(backoff * attempt)

    async def _resolve_focus_expiries(self, symbol: str) -> list[str]:
        from flow_core.orchestration.short_expiry_scanner import resolve_focus_expirations

        fetch_available = getattr(self._adapter, "fetch_available_expirations", None)
        if fetch_available is None:
            return []
        try:
            expiries = await fetch_available(symbol)
        except Exception:
            logger.exception("live_focus_expiry_lookup_failed symbol=%s", symbol)
            return []
        return resolve_focus_expirations(
            [str(exp).strip() for exp in expiries if str(exp).strip()],
            asof_date=datetime.now(timezone.utc).date(),
            focus_labels=list(self._config.live_focus_labels),
        )

    def _cadence_profile(
        self,
        *,
        base_hot_seconds: int,
        base_full_seconds: int,
    ) -> tuple[int, int, str]:
        if self._error_streak >= 2:
            return (min(base_hot_seconds * 2, 30), min(base_full_seconds * 2, 600), "backoff")
        if len(self._recent_fetch_ms) < 5:
            return (base_hot_seconds, base_full_seconds, "steady")
        samples = sorted(float(value) for value in self._recent_fetch_ms if value >= 0.0)
        if not samples:
            return (base_hot_seconds, base_full_seconds, "steady")
        idx = max(int(round(0.95 * (len(samples) - 1))), 0)
        p95_ms = samples[idx]
        if p95_ms > 0.5 * base_hot_seconds * 1000.0:
            return (min(base_hot_seconds * 2, 30), min(base_full_seconds * 2, 600), "backoff")
        return (base_hot_seconds, base_full_seconds, "steady")

    def _emit_status(self, payload: dict[str, Any]) -> None:
        if self._status_callback is None:
            return
        try:
            self._status_callback(payload)
        except Exception:  # pragma: no cover
            logger.exception("live_status_callback_failed")

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

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
    ) -> None:
        self._adapter = adapter
        self._provider_map = provider_map
        self._config = config
        self._on_batch = on_batch
        self._running = False

    async def run(
        self,
        symbol: str,
        expiration: str | None = None,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        self._running = True
        while self._running and (stop_event is None or not stop_event.is_set()):
            start = time.perf_counter()
            try:
                raw = await self._fetch_with_retry(symbol, expiration)
                mapped = map_provider_records(raw, self._provider_map, underlying_symbol=symbol)
                await self._on_batch(mapped)
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.info("live_batch_ok symbol=%s rows=%s latency_ms=%.2f", symbol, mapped.height, elapsed_ms)
            except Exception as exc:  # pragma: no cover
                logger.exception("live_batch_failed symbol=%s err=%s", symbol, exc)
            if stop_event is not None and stop_event.is_set():
                break
            await asyncio.sleep(self._config.live_poll_seconds)

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

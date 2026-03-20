from __future__ import annotations

import logging

import polars as pl

from flow_core.config.models import ProviderMap
from flow_core.ingestion.mapper import map_provider_records
from flow_core.ingestion.providers.base import ProviderAdapter

logger = logging.getLogger(__name__)


class SnapshotIngestor:
    def __init__(self, adapter: ProviderAdapter, provider_map: ProviderMap) -> None:
        self._adapter = adapter
        self._provider_map = provider_map

    async def fetch_snapshot(self, symbol: str) -> pl.DataFrame:
        raw = await self._adapter.fetch_full_snapshot(symbol)
        mapped = map_provider_records(raw, self._provider_map, underlying_symbol=symbol)
        logger.info("snapshot_ok symbol=%s rows=%s", symbol, mapped.height)
        return mapped

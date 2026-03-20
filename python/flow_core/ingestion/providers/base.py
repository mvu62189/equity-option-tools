from __future__ import annotations

from abc import ABC, abstractmethod

import polars as pl


class ProviderAdapter(ABC):
    @abstractmethod
    async def fetch_option_chain(self, symbol: str, expiration: str | None = None) -> pl.DataFrame:
        """Fetch one option chain, returning provider-native columns."""

    @abstractmethod
    async def fetch_full_snapshot(self, symbol: str) -> pl.DataFrame:
        """Fetch all available expirations for one symbol."""

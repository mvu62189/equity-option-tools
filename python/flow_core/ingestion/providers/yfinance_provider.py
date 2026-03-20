from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pandas as pd
import polars as pl
import yfinance as yf

from .base import ProviderAdapter


class YFinanceAdapter(ProviderAdapter):
    """Provider adapter that emits provider-native field names expected by D0 mapper."""

    async def fetch_option_chain(self, symbol: str, expiration: str | None = None) -> pl.DataFrame:
        return await asyncio.to_thread(self._fetch_option_chain_sync, symbol, expiration)

    async def fetch_full_snapshot(self, symbol: str) -> pl.DataFrame:
        return await asyncio.to_thread(self._fetch_full_snapshot_sync, symbol)

    @staticmethod
    def _resolve_expiries(exps: list[str], expiration: str | None) -> list[str]:
        if not exps:
            return []
        if expiration is None or expiration == "nearest":
            return [exps[0]]
        if expiration == "all":
            return list(exps)
        if expiration in {"0-7d", "0-30d"}:
            horizon_days = 7 if expiration == "0-7d" else 30
            now = datetime.now(timezone.utc).date()
            selected: list[str] = []
            for exp in exps:
                try:
                    exp_dt = datetime.fromisoformat(exp).date()
                except ValueError:
                    continue
                if 0 <= (exp_dt - now).days <= horizon_days:
                    selected.append(exp)
            return selected or [exps[0]]
        if "," in expiration:
            requested = {x.strip() for x in expiration.split(",") if x.strip()}
            selected = [exp for exp in exps if exp in requested]
            return selected or [exps[0]]
        return [expiration] if expiration in exps else [exps[0]]

    def _fetch_option_chain_sync(self, symbol: str, expiration: str | None = None) -> pl.DataFrame:
        ticker = yf.Ticker(symbol)
        exps = list(ticker.options)
        if not exps:
            return pl.DataFrame()
        now = datetime.now(timezone.utc)
        spot = float(ticker.fast_info["lastPrice"])
        selected_exps = self._resolve_expiries(exps, expiration)
        rows = []
        for selected_exp in selected_exps:
            chain = ticker.option_chain(selected_exp)
            rows.append(self._normalize_leg(chain.calls, selected_exp, "call", now, spot))
            rows.append(self._normalize_leg(chain.puts, selected_exp, "put", now, spot))
        return pl.from_pandas(pd.concat(rows, ignore_index=True))

    def _fetch_full_snapshot_sync(self, symbol: str) -> pl.DataFrame:
        ticker = yf.Ticker(symbol)
        exps = list(ticker.options)
        if not exps:
            return pl.DataFrame()

        now = datetime.now(timezone.utc)
        spot = float(ticker.fast_info["lastPrice"])
        rows = []

        for exp in exps:
            chain = ticker.option_chain(exp)
            rows.append(self._normalize_leg(chain.calls, exp, "call", now, spot))
            rows.append(self._normalize_leg(chain.puts, exp, "put", now, spot))

        return pl.from_pandas(pd.concat(rows, ignore_index=True))

    @staticmethod
    def _normalize_leg(frame: pd.DataFrame, expiration: str, option_type: str, now: datetime, spot: float) -> pd.DataFrame:
        out = frame.copy()
        out["expiration"] = expiration
        out["optionType"] = option_type
        out["asofTs"] = now
        out["underlyingPrice"] = spot
        return out

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

import polars as pl

from .state_store import BatchPayload, LiveStateStore, SymbolSnapshot


def _empty() -> pl.DataFrame:
    return pl.DataFrame()


@dataclass
class InMemoryQuoteCache:
    _state_store: LiveStateStore = field(default_factory=LiveStateStore)
    _update_callback: Callable[[str, int], None] | None = None

    async def upsert(self, symbol: str, frame: pl.DataFrame) -> None:
        await self._publish_partial(symbol, raw=frame)

    async def get(self, symbol: str) -> pl.DataFrame:
        snap = self._state_store.get_snapshot(symbol)
        return snap.raw if snap is not None else _empty()

    async def snapshot(self) -> dict[str, pl.DataFrame]:
        out: dict[str, pl.DataFrame] = {}
        for symbol in self._state_store.get_symbols():
            snap = self._state_store.get_snapshot(symbol)
            if snap is not None:
                out[symbol] = snap.raw
        return out

    async def upsert_parity(self, symbol: str, frame: pl.DataFrame) -> None:
        await self._publish_partial(symbol, parity=frame)

    async def upsert_dispatch(self, symbol: str, frame: pl.DataFrame) -> None:
        await self._publish_partial(symbol, dispatch=frame)

    async def upsert_parity_detail(self, symbol: str, frame: pl.DataFrame) -> None:
        await self._publish_partial(symbol, parity_detail=frame)

    async def upsert_ssvi(self, symbol: str, frame: pl.DataFrame) -> None:
        await self._publish_partial(symbol, ssvi=frame)

    async def upsert_greeks(self, symbol: str, frame: pl.DataFrame) -> None:
        await self._publish_partial(symbol, greeks=frame)

    async def append_calibration_diagnostics(self, symbol: str, frame: pl.DataFrame) -> None:
        prev = self.get_calibration_diagnostics_nowait(symbol)
        merged = frame if prev.is_empty() else prev.vstack(frame, in_place=False)
        await self._publish_partial(symbol, calibration_diag=merged)

    async def pop_calibration_diagnostics(self, symbol: str) -> pl.DataFrame:
        snap = self._state_store.get_snapshot(symbol)
        if snap is None:
            return _empty()
        out = snap.calibration_diag_tail
        await self._publish_partial(symbol, calibration_diag=_empty())
        return out

    async def publish_batch(self, payload: BatchPayload) -> int:
        version = self._state_store.publish(payload)
        if self._update_callback is not None:
            self._update_callback(payload.symbol, version)
        return version

    def publish_overlay_payloads(self, symbol: str, version: int, payloads: dict[str, object]) -> bool:
        return self._state_store.publish_overlay_payloads(symbol, version, payloads)

    def increment_drop_counter(self, symbol: str, dataset: str, amount: int = 1) -> None:
        self._state_store.increment_drop_counter(symbol, dataset, amount)

    def trim_to_budget(self, symbol: str) -> dict[str, int]:
        return self._state_store.trim_to_budget(symbol)

    def estimate_symbol_bytes(self, symbol: str) -> dict[str, int]:
        return self._state_store.estimate_symbol_bytes(symbol)

    def estimate_total_bytes(self) -> int:
        return self._state_store.estimate_total_bytes()

    def stats_frame(self) -> pl.DataFrame:
        return self._state_store.stats_frame()

    def get_snapshot_nowait(self, symbol: str) -> SymbolSnapshot | None:
        return self._state_store.get_snapshot(symbol)

    def get_version_nowait(self, symbol: str) -> int:
        return self._state_store.get_latest_version(symbol)

    def get_history_nowait(self, symbol: str, dataset: str) -> pl.DataFrame:
        return self._state_store.get_history(symbol, dataset)

    def append_history(self, symbol: str, dataset: str, frame: pl.DataFrame) -> None:
        self._state_store.append_history(symbol, dataset, frame)

    def set_update_callback(self, callback: Callable[[str, int], None] | None) -> None:
        self._update_callback = callback

    def get_nowait(self, symbol: str) -> pl.DataFrame:
        snap = self._state_store.get_snapshot(symbol)
        return snap.raw if snap is not None else _empty()

    def get_parity_nowait(self, symbol: str) -> pl.DataFrame:
        snap = self._state_store.get_snapshot(symbol)
        return snap.parity if snap is not None else _empty()

    def get_dispatch_nowait(self, symbol: str) -> pl.DataFrame:
        snap = self._state_store.get_snapshot(symbol)
        return snap.dispatch if snap is not None else _empty()

    def get_parity_detail_nowait(self, symbol: str) -> pl.DataFrame:
        snap = self._state_store.get_snapshot(symbol)
        return snap.parity_detail if snap is not None else _empty()

    def get_ssvi_nowait(self, symbol: str) -> pl.DataFrame:
        snap = self._state_store.get_snapshot(symbol)
        return snap.ssvi if snap is not None else _empty()

    def get_calibration_diagnostics_nowait(self, symbol: str) -> pl.DataFrame:
        snap = self._state_store.get_snapshot(symbol)
        return snap.calibration_diag_tail if snap is not None else _empty()

    def get_greeks_nowait(self, symbol: str) -> pl.DataFrame:
        snap = self._state_store.get_snapshot(symbol)
        return snap.greeks if snap is not None else _empty()

    async def _publish_partial(
        self,
        symbol: str,
        *,
        raw: pl.DataFrame | None = None,
        greeks: pl.DataFrame | None = None,
        ssvi: pl.DataFrame | None = None,
        dispatch: pl.DataFrame | None = None,
        parity: pl.DataFrame | None = None,
        parity_detail: pl.DataFrame | None = None,
        calibration_diag: pl.DataFrame | None = None,
    ) -> int:
        snap = self._state_store.get_snapshot(symbol)
        if snap is None:
            base = SymbolSnapshot(
                symbol=symbol,
                batch_id=f"{symbol}:{uuid4().hex}",
                version=0,
                updated_at_utc=datetime.now(timezone.utc),
                trading_date="",
                snapshot_kind="partial",
                source_mode="memory_partial",
                is_final_for_day=False,
                parent_batch_id="",
                raw=_empty(),
                greeks=_empty(),
                ssvi=_empty(),
                dispatch=_empty(),
                parity=_empty(),
                parity_detail=_empty(),
                calibration_diag_tail=_empty(),
                overlay_payloads={},
                memory_bytes={},
                drop_counters={},
                latency_ms={},
                status={},
            )
        else:
            base = snap

        payload = BatchPayload(
            symbol=symbol,
            batch_id=base.batch_id if snap is not None else f"{symbol}:{uuid4().hex}",
            version_hint=base.version + 1,
            updated_at_utc=datetime.now(timezone.utc),
            raw=raw if raw is not None else base.raw,
            greeks=greeks if greeks is not None else base.greeks,
            ssvi=ssvi if ssvi is not None else base.ssvi,
            dispatch=dispatch if dispatch is not None else base.dispatch,
            parity=parity if parity is not None else base.parity,
            parity_detail=parity_detail if parity_detail is not None else base.parity_detail,
            calibration_diag=calibration_diag if calibration_diag is not None else base.calibration_diag_tail,
            trading_date=base.trading_date,
            snapshot_kind=base.snapshot_kind,
            source_mode=base.source_mode,
            is_final_for_day=base.is_final_for_day,
            parent_batch_id=base.parent_batch_id,
            latency_ms=dict(base.latency_ms),
            status=dict(base.status),
        )
        return await self.publish_batch(payload)

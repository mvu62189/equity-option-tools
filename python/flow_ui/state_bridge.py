from __future__ import annotations

from threading import Lock

from PySide6.QtCore import QObject, Signal


class UIStateBridge(QObject):
    snapshot_ready = Signal(str, int)
    stats_ready = Signal(dict)

    def __init__(self, max_pending_per_symbol: int = 1) -> None:
        super().__init__()
        self._lock = Lock()
        self._latest: dict[str, int] = {}
        self._inflight: set[str] = set()
        self._dropped: dict[str, int] = {}
        self._max_pending = max(max_pending_per_symbol, 1)

    def publish(self, symbol: str, version: int) -> None:
        self.snapshot_ready.emit(symbol, version)

    def coalesce(self, symbol: str, version: int) -> None:
        with self._lock:
            prev = self._latest.get(symbol)
            if prev is not None and version <= prev:
                return
            if symbol in self._inflight and prev is not None:
                self._dropped[symbol] = self._dropped.get(symbol, 0) + 1
            self._latest[symbol] = version
            if symbol in self._inflight:
                return
            self._inflight.add(symbol)
        self.snapshot_ready.emit(symbol, version)

    def consume_latest(self, symbol: str) -> int | None:
        with self._lock:
            version = self._latest.pop(symbol, None)
            self._inflight.discard(symbol)
            return version

    def drop_count(self, symbol: str) -> int:
        with self._lock:
            return self._dropped.get(symbol, 0)

    def emit_stats(self) -> None:
        with self._lock:
            stats = {"dropped": dict(self._dropped), "pending": dict(self._latest)}
        self.stats_ready.emit(stats)


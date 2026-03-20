from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from PySide6.QtCore import QObject, Signal

from flow_core.orchestration.state_store import SymbolSnapshot
from flow_ui.viewmodels import build_overlay_payload


class UpdateCoordinator(QObject):
    overlay_ready = Signal(str, int, dict)

    def __init__(self) -> None:
        super().__init__()
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="overlay-prep")
        self._running = False
        self._pending: tuple[SymbolSnapshot, str, str, str, str, set[str], bool] | None = None

    def request_overlay(
        self,
        snapshot: SymbolSnapshot,
        greek: str,
        option_type: str,
        expiry_filter: str,
        space_mode: str = "strike",
        engine_mask: set[str] | None = None,
        dual_mode: bool = False,
    ) -> None:
        with self._lock:
            self._pending = (snapshot, greek, option_type, expiry_filter, space_mode, set(engine_mask or set()), dual_mode)
            if self._running:
                return
            self._running = True
        self._executor.submit(self._run)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self) -> None:
        while True:
            with self._lock:
                work = self._pending
                self._pending = None
            if work is None:
                with self._lock:
                    self._running = False
                return
            snapshot, greek, option_type, expiry_filter, space_mode, engine_mask, dual_mode = work
            payload = build_overlay_payload(
                snapshot,
                greek,
                option_type,
                expiry_filter,
                space_mode=space_mode,
                engine_mask=engine_mask,
                dual_mode=dual_mode,
            )
            self.overlay_ready.emit(snapshot.symbol, snapshot.version, payload)

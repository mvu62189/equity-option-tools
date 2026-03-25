from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psutil

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class StreamRunLock:
    symbol: str
    mode: str
    lock_root: Path = Path("data/runlocks")
    token: str = ""
    path: Path | None = None

    def acquire(self) -> None:
        self.lock_root.mkdir(parents=True, exist_ok=True)
        self.path = self.lock_root / f"{self.symbol.upper()}.lock"
        self.token = uuid4().hex
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        for _attempt in range(2):
            payload = self._build_payload()
            try:
                fd = os.open(str(self.path), flags)
            except FileExistsError as exc:
                holder = self._read_existing()
                stale, reason = self._is_stale_holder(holder)
                if stale:
                    logger.warning(
                        "stream_lock_reclaim_stale symbol=%s pid=%s reason=%s",
                        self.symbol.upper(),
                        holder.get("pid", "unknown"),
                        reason,
                    )
                    self._unlink_stale_lock()
                    continue
                raise RuntimeError(
                    f"stream lock already held for {self.symbol.upper()} by mode="
                    f"{holder.get('mode', 'unknown')} pid={holder.get('pid', 'unknown')}"
                ) from exc
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload))
            return
        raise RuntimeError(f"failed to acquire stream lock for {self.symbol.upper()} after reclaiming stale holder")

    def release(self) -> None:
        if self.path is None or not self.path.exists():
            return
        holder = self._read_existing()
        if holder.get("token") == self.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass

    def _read_existing(self) -> dict:
        if self.path is None or not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _build_payload(self) -> dict[str, object]:
        process = psutil.Process()
        create_time = float(process.create_time())
        return {
            "symbol": self.symbol.upper(),
            "mode": self.mode,
            "pid": os.getpid(),
            "pid_create_time": create_time,
            "pid_started_utc": datetime.fromtimestamp(create_time, timezone.utc).isoformat(),
            "token": self.token,
            "ts_utc": datetime.now(timezone.utc).isoformat(),
        }

    def _is_stale_holder(self, holder: dict) -> tuple[bool, str]:
        pid_raw = holder.get("pid")
        try:
            pid = int(pid_raw)
        except (TypeError, ValueError):
            return False, "holder_pid_unreadable"

        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return True, "holder_pid_missing"
        except psutil.Error:
            return False, "holder_pid_unverifiable"

        recorded_create_time = holder.get("pid_create_time")
        if recorded_create_time is None:
            return False, "holder_pid_active_legacy"

        try:
            expected = float(recorded_create_time)
            actual = float(process.create_time())
        except (TypeError, ValueError, OSError, psutil.Error):
            return False, "holder_pid_unverifiable"

        if abs(actual - expected) > 1.0:
            return True, "holder_pid_reused"
        return False, "holder_pid_active"

    def _unlink_stale_lock(self) -> None:
        if self.path is None:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            return

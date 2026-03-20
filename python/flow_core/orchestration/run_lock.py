from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


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
        payload = {
            "symbol": self.symbol.upper(),
            "mode": self.mode,
            "pid": os.getpid(),
            "token": self.token,
            "ts_utc": datetime.now(timezone.utc).isoformat(),
        }
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(str(self.path), flags)
        except FileExistsError as exc:
            holder = self._read_existing()
            raise RuntimeError(
                f"stream lock already held for {self.symbol.upper()} by mode="
                f"{holder.get('mode', 'unknown')} pid={holder.get('pid', 'unknown')}"
            ) from exc
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload))

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

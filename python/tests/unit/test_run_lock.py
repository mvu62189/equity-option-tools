from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from flow_core.orchestration.run_lock import StreamRunLock


def _write_lock(path, payload: dict[str, object]) -> None:  # noqa: ANN001
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_stream_lock_reclaims_dead_holder(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    lock_path = tmp_path / "SPY.lock"
    _write_lock(
        lock_path,
        {
            "symbol": "SPY",
            "mode": "ui_live",
            "pid": 17332,
            "token": "old-token",
            "ts_utc": datetime.now(timezone.utc).isoformat(),
        },
    )

    def _fake_process(pid=None):  # noqa: ANN001
        if pid is None:
            class _CurrentProcess:
                def create_time(self) -> float:
                    return 1000.0

            return _CurrentProcess()
        raise __import__("psutil").NoSuchProcess(pid)

    monkeypatch.setattr("flow_core.orchestration.run_lock.psutil.Process", _fake_process)
    monkeypatch.setattr("flow_core.orchestration.run_lock.os.getpid", lambda: 99999)

    lock = StreamRunLock(symbol="SPY", mode="ui_live", lock_root=tmp_path)
    lock.acquire()

    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == 99999
    assert payload["symbol"] == "SPY"
    assert payload["pid_create_time"] == 1000.0


def test_stream_lock_blocks_live_holder(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    lock_path = tmp_path / "SPY.lock"
    _write_lock(
        lock_path,
        {
            "symbol": "SPY",
            "mode": "ui_live",
            "pid": 17332,
            "pid_create_time": 2000.0,
            "token": "old-token",
            "ts_utc": datetime.now(timezone.utc).isoformat(),
        },
    )

    def _fake_process(pid=None):  # noqa: ANN001
        class _Process:
            def create_time(self) -> float:
                return 2000.0 if pid == 17332 else 3000.0

        return _Process()

    monkeypatch.setattr("flow_core.orchestration.run_lock.psutil.Process", _fake_process)

    lock = StreamRunLock(symbol="SPY", mode="ui_live", lock_root=tmp_path)
    with pytest.raises(RuntimeError, match="stream lock already held"):
        lock.acquire()


def test_stream_lock_reclaims_pid_reuse(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    lock_path = tmp_path / "SPY.lock"
    _write_lock(
        lock_path,
        {
            "symbol": "SPY",
            "mode": "ui_live",
            "pid": 17332,
            "pid_create_time": 2000.0,
            "token": "old-token",
            "ts_utc": datetime.now(timezone.utc).isoformat(),
        },
    )

    def _fake_process(pid=None):  # noqa: ANN001
        class _Process:
            def create_time(self) -> float:
                if pid == 17332:
                    return 4000.0
                return 5000.0

        return _Process()

    monkeypatch.setattr("flow_core.orchestration.run_lock.psutil.Process", _fake_process)
    monkeypatch.setattr("flow_core.orchestration.run_lock.os.getpid", lambda: 22222)

    lock = StreamRunLock(symbol="SPY", mode="ui_live", lock_root=tmp_path)
    lock.acquire()

    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload["pid"] == 22222
    assert payload["pid_create_time"] == 5000.0

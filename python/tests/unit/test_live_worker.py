from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from flow_core.config.models import PipelineConfig, ProviderMap
from flow_core.ingestion.live_worker import LiveIngestionWorker


@pytest.mark.asyncio
async def test_live_worker_uses_dynamic_expiration_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    statuses: list[dict[str, object]] = []
    selected = {"expiration": "2026-04-17"}

    class _Adapter:
        async def fetch_option_chain(self, symbol: str, expiration: str | None = None) -> pl.DataFrame:
            seen.append(expiration)
            if len(seen) == 1:
                selected["expiration"] = "2026-05-15"
            return pl.DataFrame({"symbol": [symbol], "expiration": [expiration or "auto"]})

    async def _on_batch(frame: pl.DataFrame) -> None:
        if len(seen) >= 2:
            worker.stop()

    monkeypatch.setattr("flow_core.ingestion.live_worker.map_provider_records", lambda raw, provider_map, underlying_symbol: raw)

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("flow_core.ingestion.live_worker.asyncio.sleep", _no_sleep)

    worker = LiveIngestionWorker(
        adapter=_Adapter(),
        provider_map=ProviderMap(provider="test", required_fields=[], field_map={}),
        config=PipelineConfig(live_poll_seconds=1, max_retries=0, retry_backoff_seconds=0.0),
        on_batch=_on_batch,
        status_callback=statuses.append,
    )

    await worker.run(
        symbol="SPY",
        expiration_resolver=lambda: selected["expiration"],
    )

    assert seen[:2] == ["2026-04-17", "2026-05-15"]
    assert any(status.get("state") == "ok" for status in statuses)


@pytest.mark.asyncio
async def test_live_worker_emits_empty_status_when_provider_returns_no_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses: list[dict[str, object]] = []
    worker: LiveIngestionWorker | None = None

    class _Adapter:
        async def fetch_option_chain(self, symbol: str, expiration: str | None = None) -> pl.DataFrame:
            return pl.DataFrame()

    async def _on_batch(frame: pl.DataFrame) -> None:
        raise AssertionError("empty batches should not reach on_batch")

    async def _no_sleep(_seconds: float) -> None:
        return None

    def _status(payload: dict[str, object]) -> None:
        statuses.append(payload)
        if payload.get("state") == "empty" and worker is not None:
            worker.stop()

    monkeypatch.setattr("flow_core.ingestion.live_worker.map_provider_records", lambda raw, provider_map, underlying_symbol: raw)
    monkeypatch.setattr("flow_core.ingestion.live_worker.asyncio.sleep", _no_sleep)

    worker = LiveIngestionWorker(
        adapter=_Adapter(),
        provider_map=ProviderMap(provider="test", required_fields=[], field_map={}),
        config=PipelineConfig(live_poll_seconds=1, max_retries=0, retry_backoff_seconds=0.0),
        on_batch=_on_batch,
        status_callback=_status,
    )

    await worker.run(symbol="SPY", expiration_resolver=lambda: None)

    assert any(status.get("state") == "empty" for status in statuses)


@pytest.mark.asyncio
async def test_live_worker_uses_focused_short_expiries_and_emits_cadence_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_expirations: list[str | None] = []
    statuses: list[dict[str, object]] = []

    class _Adapter:
        async def fetch_available_expirations(self, symbol: str) -> list[str]:
            return ["2026-03-02", "2026-03-03", "2026-03-06", "2026-03-20"]

        async def fetch_option_chain(self, symbol: str, expiration: str | None = None) -> pl.DataFrame:
            seen_expirations.append(expiration)
            return pl.DataFrame({"symbol": [symbol], "expiration": [expiration or "auto"]})

    async def _on_batch(frame: pl.DataFrame) -> None:
        worker.stop()

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("flow_core.ingestion.live_worker.map_provider_records", lambda raw, provider_map, underlying_symbol: raw)
    monkeypatch.setattr("flow_core.ingestion.live_worker.asyncio.sleep", _no_sleep)
    monkeypatch.setattr(
        "flow_core.ingestion.live_worker.datetime",
        type(
            "_DateTimeProxy",
            (),
            {"now": staticmethod(lambda tz=None: dt.datetime(2026, 3, 2, tzinfo=tz or dt.timezone.utc))},
        ),
    )

    worker = LiveIngestionWorker(
        adapter=_Adapter(),
        provider_map=ProviderMap(provider="test", required_fields=[], field_map={}),
        config=PipelineConfig(
            live_hot_poll_seconds=5,
            live_full_snapshot_poll_seconds=60,
            max_retries=0,
            retry_backoff_seconds=0.0,
            live_focus_labels=["0DTE", "1DTE", "EOW"],
        ),
        on_batch=_on_batch,
        status_callback=statuses.append,
    )

    await worker.run(symbol="SPY", expiration_resolver=lambda: None)

    assert seen_expirations == ["2026-03-02,2026-03-03,2026-03-06"]
    ok_status = next(status for status in statuses if status.get("state") == "ok")
    assert ok_status["fetch_scope"] == "focused_short"
    assert ok_status["cadence_mode"] == "steady"
    assert ok_status["cadence_hot_seconds"] == 5
    assert ok_status["cadence_full_snapshot_seconds"] == 60

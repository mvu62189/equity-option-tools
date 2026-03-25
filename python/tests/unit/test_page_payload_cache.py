from __future__ import annotations

from flow_ui.page_payload_cache import PagePayloadCache


def test_page_payload_cache_reuses_payload_within_same_batch() -> None:
    cache = PagePayloadCache(max_entries=4)
    calls = {"count": 0}

    def _build() -> dict[str, int]:
        calls["count"] += 1
        return {"value": calls["count"]}

    first = cache.get_or_build(batch_id="b1", page="scanner", key=("0DTE",), builder=_build)
    second = cache.get_or_build(batch_id="b1", page="scanner", key=("0DTE",), builder=_build)

    assert first == {"value": 1}
    assert second == {"value": 1}
    assert calls["count"] == 1


def test_page_payload_cache_clears_entries_when_batch_changes() -> None:
    cache = PagePayloadCache(max_entries=4)
    calls = {"count": 0}

    def _build() -> dict[str, int]:
        calls["count"] += 1
        return {"value": calls["count"]}

    cache.get_or_build(batch_id="b1", page="scanner", key=("0DTE",), builder=_build)
    rebuilt = cache.get_or_build(batch_id="b2", page="scanner", key=("0DTE",), builder=_build)

    assert rebuilt == {"value": 2}
    assert calls["count"] == 2

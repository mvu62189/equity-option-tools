from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Hashable


@dataclass(slots=True)
class PagePayloadCache:
    max_entries: int = 128
    _batch_id: str = ""
    _entries: OrderedDict[tuple[str, tuple[Hashable, ...]], Any] = field(default_factory=OrderedDict)

    def clear(self) -> None:
        self._batch_id = ""
        self._entries.clear()

    def bind_batch(self, batch_id: str) -> None:
        normalized = str(batch_id or "")
        if normalized == self._batch_id:
            return
        self._batch_id = normalized
        self._entries.clear()

    def get_or_build(
        self,
        *,
        batch_id: str,
        page: str,
        key: tuple[Hashable, ...],
        builder: Callable[[], Any],
    ) -> Any:
        self.bind_batch(batch_id)
        cache_key = (str(page), tuple(key))
        if cache_key in self._entries:
            self._entries.move_to_end(cache_key)
            return self._entries[cache_key]
        payload = builder()
        self._entries[cache_key] = payload
        while len(self._entries) > max(int(self.max_entries), 1):
            self._entries.popitem(last=False)
        return payload

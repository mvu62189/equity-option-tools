from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from flow_core.orchestration.cache import InMemoryQuoteCache
from flow_ui.main import MainWindow


def test_ui_uses_cache_contract(qtbot) -> None:
    cache = InMemoryQuoteCache()
    window = MainWindow(cache=cache, refresh_ms=1000, symbol="SPY")
    qtbot.addWidget(window)
    assert window.windowTitle() == "Quant Pipeline MVP"

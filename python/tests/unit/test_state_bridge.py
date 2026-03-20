from __future__ import annotations

from flow_ui.state_bridge import UIStateBridge


def test_state_bridge_latest_wins() -> None:
    bridge = UIStateBridge(max_pending_per_symbol=1)
    bridge.coalesce("SPY", 1)
    bridge.coalesce("SPY", 2)
    bridge.coalesce("SPY", 3)
    latest = bridge.consume_latest("SPY")
    assert latest == 3
    assert bridge.drop_count("SPY") >= 1


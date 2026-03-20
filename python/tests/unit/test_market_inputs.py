from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from flow_core.quant.market_inputs import HybridDividendSource, TBillRateCurve


def test_tbill_rate_curve_short_end_uses_irx(monkeypatch) -> None:
    def _fake_latest(symbol: str) -> float:
        if symbol == "^IRX":
            return 4.5
        if symbol == "^FVX":
            return 4.0
        if symbol == "^TNX":
            return 4.2
        raise RuntimeError("unknown")

    monkeypatch.setattr("flow_core.quant.market_inputs._latest_price", _fake_latest)
    curve = TBillRateCurve(refresh_seconds=3600, use_pchip=True)
    assert abs(curve.rate(0.1) - 0.045) < 1e-12
    assert curve.rate(5.0) > 0.0


def test_hybrid_dividend_source_projects_forward(monkeypatch) -> None:
    class _FakeTicker:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol
            now = datetime(2026, 1, 1, tzinfo=timezone.utc)
            idx = [now - timedelta(days=91 * i) for i in range(4, 0, -1)]
            self.dividends = pd.Series([1.0, 1.0, 1.0, 1.0], index=idx)

    monkeypatch.setattr("flow_core.quant.market_inputs.yf.Ticker", _FakeTicker)

    src = HybridDividendSource(projection_horizon_years=2.0, lookback_events=6)
    asof = datetime(2026, 2, 1, tzinfo=timezone.utc)
    out = src.projected_dividends(symbol="SPY", asof_ts=asof, tau_years=1.0)
    assert len(out) >= 1
    assert all(d.amount > 0.0 and d.time_to_ex_date > 0.0 for d in out)

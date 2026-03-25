from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl

from flow_core.orchestration.quote_quality import build_quote_quality
from flow_core.quant.bs import BSInput, price_euro_bs


def _call_row(strike: float, *, bid: float, ask: float, vendor_iv: float) -> dict[str, object]:
    return {
        "symbol": "SPY",
        "contract_symbol": f"SPY_20260619_C_{int(strike * 1000):08d}",
        "asof_ts": datetime(2026, 3, 1, 15, 0, tzinfo=timezone.utc),
        "expiration": date(2026, 6, 19),
        "option_type": "call",
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "last": 0.5 * (bid + ask),
        "volume": 100,
        "open_interest": 200,
        "underlying_price": 100.0,
        "implied_vol_vendor": vendor_iv,
        "dividend_policy": "",
    }


def test_quote_quality_drops_one_sided_markets() -> None:
    frame = pl.DataFrame(
        [
            _call_row(95.0, bid=7.5, ask=7.7, vendor_iv=0.9),
            _call_row(100.0, bid=0.0, ask=4.3, vendor_iv=0.9),
            _call_row(105.0, bid=2.1, ask=2.3, vendor_iv=0.9),
        ]
    )

    bundle = build_quote_quality(frame)
    dropped = bundle.points.filter(pl.col("contract_symbol").str.contains("100000"))
    assert dropped.height == 1
    assert bool(dropped["one_sided_market"][0]) is True
    assert dropped["drop_reason"][0] == "one_sided_market"


def test_quote_quality_prefers_self_derived_iv_over_vendor_iv() -> None:
    vol = 0.20
    rows: list[dict[str, object]] = []
    for strike in (95.0, 100.0, 105.0):
        mid = float(
            price_euro_bs(
                BSInput(
                    spot=100.0,
                    strike=strike,
                    rate=0.04,
                    dividend=0.0,
                    tau=0.30,
                    vol=vol,
                    is_call=True,
                )
            ).price
        )
        rows.append(_call_row(strike, bid=mid - 0.10, ask=mid + 0.10, vendor_iv=1.50))

    bundle = build_quote_quality(pl.DataFrame(rows), rate=0.04)

    assert bundle.calibration_input.height == 3
    inputs = bundle.calibration_input["implied_vol_input"].to_list()
    assert all(float(iv) > 0.0 for iv in inputs)
    assert max(abs(float(iv) - vol) for iv in inputs) < 0.25
    assert max(abs(float(iv) - 1.50) for iv in inputs) > 0.50

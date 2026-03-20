from __future__ import annotations

import math

from flow_core.quant.dividends import DividendEvent
from flow_core.quant.models import AmericanContract
from flow_core.quant.tree_richardson import greeks_tree_richardson


def test_tree_richardson_returns_finite_greeks() -> None:
    contract = AmericanContract(
        spot=100.0,
        strike=100.0,
        rate=0.04,
        dividend=0.0,
        tau=20.0 / 365.25,
        is_call=True,
    )
    out = greeks_tree_richardson(contract, vol=0.20, steps=80)
    assert out.success
    assert out.price >= 0.0
    assert math.isfinite(out.delta)
    assert math.isfinite(out.gamma)
    assert math.isfinite(out.theta)


def test_tree_richardson_node_event_dividend_reduces_call_value() -> None:
    contract = AmericanContract(
        spot=100.0,
        strike=100.0,
        rate=0.04,
        dividend=0.0,
        tau=20.0 / 365.25,
        is_call=True,
    )
    no_div = greeks_tree_richardson(contract, vol=0.20, steps=80)
    with_div = greeks_tree_richardson(
        contract,
        vol=0.20,
        steps=80,
        divs=[DividendEvent(amount=1.0, time_to_ex_date=10.0 / 365.25)],
    )
    assert no_div.success and with_div.success
    assert with_div.price <= no_div.price

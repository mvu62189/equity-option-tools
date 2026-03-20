from __future__ import annotations

import math

from flow_core.quant.dividends import DividendEvent
from flow_core.quant.fdm_cn import price_greeks_crank_nicolson
from flow_core.quant.models import AmericanContract


def test_crank_nicolson_returns_finite_ultrashort_greeks() -> None:
    contract = AmericanContract(
        spot=100.0,
        strike=100.0,
        rate=0.04,
        dividend=0.0,
        tau=3.0 / 365.25,
        is_call=True,
    )
    out = price_greeks_crank_nicolson(contract, vol=0.25, s_steps=120, t_steps=120)
    assert out.success
    assert out.scheme == "log"
    assert out.price >= 0.0
    assert math.isfinite(out.delta)
    assert math.isfinite(out.gamma)
    assert math.isfinite(out.theta)


def test_crank_nicolson_node_event_dividend_reduces_call_price() -> None:
    contract = AmericanContract(
        spot=100.0,
        strike=100.0,
        rate=0.04,
        dividend=0.0,
        tau=4.0 / 365.25,
        is_call=True,
    )
    no_div = price_greeks_crank_nicolson(contract, vol=0.22, s_steps=100, t_steps=120)
    with_div = price_greeks_crank_nicolson(
        contract,
        vol=0.22,
        s_steps=100,
        t_steps=120,
        divs=[DividendEvent(amount=0.8, time_to_ex_date=2.0 / 365.25)],
    )
    assert no_div.success and with_div.success
    assert with_div.price <= no_div.price


def test_crank_nicolson_can_compare_linear_scheme() -> None:
    contract = AmericanContract(
        spot=100.0,
        strike=100.0,
        rate=0.04,
        dividend=0.0,
        tau=4.0 / 365.25,
        is_call=True,
    )
    out = price_greeks_crank_nicolson(contract, vol=0.22, include_comparison=True)
    assert out.success
    assert out.comparison_price is not None
    assert out.comparison_price >= 0.0

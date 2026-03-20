from __future__ import annotations

from flow_core.quant.bs import price_euro_bs
from flow_core.quant.dividends import DividendEvent
from flow_core.quant.models import AmericanContract, BSInput
from flow_core.quant.o4o5_engines import (
    BjerksundStenslandEngine,
    american_binomial_price,
    estimate_luba_proxy_eep,
    implied_vol_american,
)


def test_american_binomial_not_below_european_for_put() -> None:
    contract = AmericanContract(
        spot=100.0,
        strike=105.0,
        rate=0.04,
        dividend=0.0,
        tau=0.25,
        is_call=False,
    )
    vol = 0.25
    am = american_binomial_price(contract, vol, steps=120)
    eu = price_euro_bs(
        BSInput(
            spot=contract.spot,
            strike=contract.strike,
            rate=contract.rate,
            dividend=contract.dividend,
            tau=contract.tau,
            vol=vol,
            is_call=False,
        )
    ).price
    assert am >= eu


def test_implied_vol_american_recovers_target_price() -> None:
    contract = AmericanContract(
        spot=100.0,
        strike=100.0,
        rate=0.03,
        dividend=0.01,
        tau=0.2,
        is_call=False,
    )
    target = american_binomial_price(contract, 0.22, steps=120)
    iv, ok = implied_vol_american(target, contract, steps=120)
    assert ok
    assert abs(iv - 0.22) < 0.03


def test_bjerksund_engine_returns_nonnegative_eep() -> None:
    contract = AmericanContract(
        spot=100.0,
        strike=98.0,
        rate=0.04,
        dividend=0.0,
        tau=0.15,
        is_call=True,
    )
    market_price = 5.0
    engine = BjerksundStenslandEngine(steps=100)
    diag = engine.estimate_eep(market_price=market_price, contract=contract)
    assert diag.eep >= 0.0


def test_luba_proxy_call_no_dividend_returns_zero_eep() -> None:
    eep = estimate_luba_proxy_eep(
        market_price=2.0,
        option_type="call",
        spot=100.0,
        strike=100.0,
        tau=0.2,
        rate=0.04,
        dividend=0.0,
    )
    assert eep == 0.0


def test_tree_node_event_dividend_reduces_call_value() -> None:
    contract = AmericanContract(
        spot=100.0,
        strike=100.0,
        rate=0.04,
        dividend=0.0,
        tau=20.0 / 365.25,
        is_call=True,
    )
    no_div = american_binomial_price(contract, 0.20, steps=120, force_zero_q=True)
    with_div = american_binomial_price(
        contract,
        0.20,
        steps=120,
        divs=[DividendEvent(amount=1.0, time_to_ex_date=10.0 / 365.25)],
        force_zero_q=True,
    )
    assert with_div <= no_div

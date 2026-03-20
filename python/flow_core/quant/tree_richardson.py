from __future__ import annotations

from dataclasses import dataclass

from .conventions import theta_one_day_forward
from .dividends import DividendEvent
from .models import AmericanContract
from .o4o5_engines import american_binomial_price


@dataclass(slots=True)
class TreeGreeksResult:
    price: float
    delta: float
    gamma: float
    theta: float
    success: bool


def _richardson_price(
    contract: AmericanContract,
    vol: float,
    steps: int,
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
) -> float:
    p_n = american_binomial_price(contract, vol, steps=steps, divs=divs, force_zero_q=True)
    p_2n = american_binomial_price(contract, vol, steps=2 * steps, divs=divs, force_zero_q=True)
    return 2.0 * p_2n - p_n


def price_tree_richardson(
    contract: AmericanContract,
    vol: float,
    steps: int = 120,
    divs: list[DividendEvent] | tuple[DividendEvent, ...] = (),
) -> float:
    if contract.tau <= 0.0:
        return max(contract.spot - contract.strike, 0.0) if contract.is_call else max(contract.strike - contract.spot, 0.0)
    return _richardson_price(contract, vol, steps=steps, divs=divs)


def greeks_tree_richardson(
    contract: AmericanContract,
    vol: float,
    steps: int = 120,
    divs: list[DividendEvent] | tuple[DividendEvent, ...] = (),
) -> TreeGreeksResult:
    if contract.spot <= 0.0 or contract.strike <= 0.0 or contract.tau <= 0.0 or vol <= 0.0:
        return TreeGreeksResult(price=float("nan"), delta=float("nan"), gamma=float("nan"), theta=float("nan"), success=False)

    price = _richardson_price(contract, vol, steps=steps, divs=divs)

    ds = max(0.5, 0.01 * contract.spot)
    up = AmericanContract(
        spot=contract.spot + ds,
        strike=contract.strike,
        rate=contract.rate,
        dividend=0.0,
        tau=contract.tau,
        is_call=contract.is_call,
    )
    dn = AmericanContract(
        spot=max(contract.spot - ds, 1e-6),
        strike=contract.strike,
        rate=contract.rate,
        dividend=0.0,
        tau=contract.tau,
        is_call=contract.is_call,
    )

    p_up = _richardson_price(up, vol, steps=steps, divs=divs)
    p_dn = _richardson_price(dn, vol, steps=steps, divs=divs)
    delta = (p_up - p_dn) / (2.0 * ds)
    gamma = (p_up - 2.0 * price + p_dn) / (ds * ds)

    dt_day = 1.0 / 365.0
    tau_prev = max(contract.tau - dt_day, 1e-8)
    prev = AmericanContract(
        spot=contract.spot,
        strike=contract.strike,
        rate=contract.rate,
        dividend=0.0,
        tau=tau_prev,
        is_call=contract.is_call,
    )
    price_prev = _richardson_price(prev, vol, steps=steps, divs=divs)
    theta = theta_one_day_forward(value_t=price, value_t_minus_dt=price_prev)

    return TreeGreeksResult(price=price, delta=delta, gamma=gamma, theta=theta, success=True)

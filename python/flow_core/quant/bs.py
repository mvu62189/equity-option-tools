from __future__ import annotations

import math

from .models import BSInput, BSResult


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def price_euro_bs(inputs: BSInput) -> BSResult:
    if inputs.tau <= 0.0 or inputs.vol <= 0.0:
        return BSResult(price=float("nan"))

    sqrt_t = math.sqrt(inputs.tau)
    d1 = (
        math.log(inputs.spot / inputs.strike)
        + (inputs.rate - inputs.dividend + 0.5 * inputs.vol * inputs.vol) * inputs.tau
    ) / (inputs.vol * sqrt_t)
    d2 = d1 - inputs.vol * sqrt_t

    disc_r = math.exp(-inputs.rate * inputs.tau)
    disc_q = math.exp(-inputs.dividend * inputs.tau)

    if inputs.is_call:
        price = inputs.spot * disc_q * _norm_cdf(d1) - inputs.strike * disc_r * _norm_cdf(d2)
    else:
        price = inputs.strike * disc_r * _norm_cdf(-d2) - inputs.spot * disc_q * _norm_cdf(-d1)

    return BSResult(price=price)

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


def implied_vol_euro_bs(
    target_price: float,
    inputs: BSInput,
    *,
    low: float = 1e-6,
    high: float = 4.0,
    max_iter: int = 100,
    tol: float = 1e-7,
) -> float:
    if (
        not math.isfinite(target_price)
        or target_price <= 0.0
        or not math.isfinite(inputs.spot)
        or not math.isfinite(inputs.strike)
        or not math.isfinite(inputs.rate)
        or not math.isfinite(inputs.dividend)
        or not math.isfinite(inputs.tau)
        or inputs.spot <= 0.0
        or inputs.strike <= 0.0
        or inputs.tau <= 0.0
    ):
        return float("nan")

    def objective(vol: float) -> float:
        return price_euro_bs(
            BSInput(
                spot=inputs.spot,
                strike=inputs.strike,
                rate=inputs.rate,
                dividend=inputs.dividend,
                tau=inputs.tau,
                vol=vol,
                is_call=inputs.is_call,
            )
        ).price - target_price

    f_low = objective(low)
    f_high = objective(high)
    if not all(math.isfinite(x) for x in (f_low, f_high)) or f_low * f_high > 0.0:
        return float("nan")

    lo = low
    hi = high
    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        f_mid = objective(mid)
        if not math.isfinite(f_mid):
            return float("nan")
        if abs(f_mid) <= tol or abs(hi - lo) <= tol:
            return float(mid)
        if f_low * f_mid <= 0.0:
            hi = mid
            f_high = f_mid
        else:
            lo = mid
            f_low = f_mid
    return float(0.5 * (lo + hi))

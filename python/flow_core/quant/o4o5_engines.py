from __future__ import annotations

import math
from typing import Protocol

from scipy.optimize import brentq

from .bs import price_euro_bs
from .conventions import terminal_boundary_call, terminal_boundary_put
from .dividends import DividendEvent, escrowed_spot
from .luba_rim import (
    LubaRimDiagnostics,
    calc_luba_2pt_call,
    calc_luba_2pt_call_with_diagnostics,
    calc_luba_2pt_put,
    calc_luba_2pt_put_with_diagnostics,
    calc_rim_call,
    calc_rim_call_with_diagnostics,
    calc_rim_put,
    calc_rim_put_with_diagnostics,
)
from .models import AmericanContract, AmericanIVDiagnostics, BSInput

try:  # optional compiled fast path
    import quantcore  # type: ignore
except Exception:  # pragma: no cover
    quantcore = None


def _to_cpp_divs(divs: list[DividendEvent] | tuple[DividendEvent, ...]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for d in divs:
        amt = float(d.amount)
        t = float(d.time_to_ex_date)
        if amt > 0.0 and t > 0.0:
            out.append((amt, t))
    return out


def _build_dividend_step_map(
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
    tau: float,
    steps: int,
) -> dict[int, float]:
    if not divs or tau <= 0.0 or steps < 2:
        return {}

    dt = tau / steps
    out: dict[int, float] = {}
    for div in divs:
        t = float(div.time_to_ex_date)
        amt = float(div.amount)
        if amt <= 0.0 or t <= 0.0 or t >= tau:
            continue
        # Snap event to the nearest lattice step so the jump condition is applied on-node.
        idx = int(round(t / dt))
        idx = max(1, min(steps - 1, idx))
        out[idx] = out.get(idx, 0.0) + amt
    return out


def _linear_interp(x: float, xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n == 0:
        return float("nan")
    if n == 1:
        return ys[0]
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]

    lo = 0
    hi = n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid

    x0, x1 = xs[lo], xs[hi]
    y0, y1 = ys[lo], ys[hi]
    if abs(x1 - x0) < 1e-12:
        return y0
    w = (x - x0) / (x1 - x0)
    return y0 + w * (y1 - y0)


def american_binomial_price(
    contract: AmericanContract,
    vol: float,
    steps: int = 120,
    divs: list[DividendEvent] | tuple[DividendEvent, ...] = (),
    force_zero_q: bool = False,
) -> float:
    if (
        contract.spot <= 0.0
        or contract.strike <= 0.0
        or contract.tau <= 0.0
        or vol <= 0.0
        or steps < 2
    ):
        return float("nan")

    dt = contract.tau / steps
    if dt <= 0.0:
        return float("nan")

    up = math.exp(vol * math.sqrt(dt))
    down = 1.0 / up
    disc = math.exp(-contract.rate * dt)
    q_eff = 0.0 if force_zero_q else contract.dividend
    growth = math.exp((contract.rate - q_eff) * dt)
    denom = up - down
    if abs(denom) < 1e-12:
        return float("nan")
    p = (growth - down) / denom
    p = min(1.0, max(0.0, p))

    dividend_steps = _build_dividend_step_map(divs=divs, tau=contract.tau, steps=steps)

    values = [0.0] * (steps + 1)
    for j in range(steps + 1):
        spot_t = contract.spot * (up**j) * (down ** (steps - j))
        if contract.is_call:
            values[j] = max(spot_t - contract.strike, 0.0)
        else:
            values[j] = max(contract.strike - spot_t, 0.0)

    for i in range(steps - 1, -1, -1):
        level = [0.0] * (i + 1)
        for j in range(i + 1):
            level[j] = disc * (p * values[j + 1] + (1.0 - p) * values[j])

        jump = dividend_steps.get(i)
        if jump is not None and jump > 0.0:
            spots_i = [contract.spot * (up**j) * (down ** (i - j)) for j in range(i + 1)]
            mapped = [_linear_interp(max(s_pre - jump, 1e-12), spots_i, level) for s_pre in spots_i]
            level = mapped

        for j in range(i + 1):
            spot_t = contract.spot * (up**j) * (down ** (i - j))
            if contract.is_call:
                exercise = max(spot_t - contract.strike, 0.0)
            else:
                exercise = max(contract.strike - spot_t, 0.0)
            level[j] = max(level[j], exercise)

        values = level
    return float(values[0])


def implied_vol_american(
    target_price: float,
    contract: AmericanContract,
    steps: int = 120,
    low: float = 1e-4,
    high: float = 4.0,
    divs: list[DividendEvent] | tuple[DividendEvent, ...] = (),
    force_zero_q: bool = False,
) -> tuple[float, bool]:
    if target_price <= 0.0:
        return float("nan"), False

    def objective(sigma: float) -> float:
        return (
            american_binomial_price(
                contract,
                sigma,
                steps=steps,
                divs=divs,
                force_zero_q=force_zero_q,
            )
            - target_price
        )

    try:
        f_low = objective(low)
        f_high = objective(high)
    except Exception:
        return float("nan"), False

    if math.isnan(f_low) or math.isnan(f_high):
        return float("nan"), False

    if f_low * f_high > 0:
        return float("nan"), False

    try:
        iv = brentq(objective, low, high, maxiter=100, xtol=1e-7)
        return float(iv), True
    except Exception:
        return float("nan"), False


def implied_vol_bs2002_call(
    target_price: float,
    contract: AmericanContract,
    divs: list[DividendEvent] | tuple[DividendEvent, ...] = (),
    low: float = 1e-4,
    high: float = 4.0,
) -> tuple[float, bool]:
    if quantcore is None or target_price <= 0.0 or not contract.is_call:
        return float("nan"), False

    cpp_divs = _to_cpp_divs(divs)

    def objective(sigma: float) -> float:
        return float(
            quantcore.bs2002_escrowed_call(
                contract.spot,
                contract.strike,
                contract.tau,
                contract.rate,
                sigma,
                cpp_divs,
            )
            - target_price
        )

    try:
        f_low = objective(low)
        f_high = objective(high)
        if math.isnan(f_low) or math.isnan(f_high) or f_low * f_high > 0:
            return float("nan"), False
        iv = brentq(objective, low, high, maxiter=100, xtol=1e-7)
        return float(iv), True
    except Exception:
        return float("nan"), False


def implied_vol_bs2002_put(
    target_price: float,
    contract: AmericanContract,
    divs: list[DividendEvent] | tuple[DividendEvent, ...] = (),
    low: float = 1e-4,
    high: float = 4.0,
) -> tuple[float, bool]:
    if quantcore is None or target_price <= 0.0 or contract.is_call:
        return float("nan"), False

    cpp_divs = _to_cpp_divs(divs)

    def objective(sigma: float) -> float:
        return float(
            quantcore.bs2002_escrowed_put(
                contract.spot,
                contract.strike,
                contract.tau,
                contract.rate,
                sigma,
                cpp_divs,
            )
            - target_price
        )

    try:
        f_low = objective(low)
        f_high = objective(high)
        if math.isnan(f_low) or math.isnan(f_high) or f_low * f_high > 0:
            return float("nan"), False
        iv = brentq(objective, low, high, maxiter=100, xtol=1e-7)
        return float(iv), True
    except Exception:
        return float("nan"), False


class O4IVEngine(Protocol):
    def estimate_eep(
        self,
        market_price: float,
        contract: AmericanContract,
    ) -> AmericanIVDiagnostics:
        ...


class BjerksundStenslandEngine:
    """O4 engine adapter using compiled BS2002 fast path when available.

    Falls back to robust American binomial proxy when compiled bindings are unavailable.
    """

    def __init__(self, steps: int = 120) -> None:
        self.steps = steps

    def estimate_eep(
        self,
        market_price: float,
        contract: AmericanContract,
        divs: list[DividendEvent] | tuple[DividendEvent, ...] = (),
        force_zero_q: bool = False,
    ) -> AmericanIVDiagnostics:
        if quantcore is not None:
            iv_cpp, ok_cpp = (
                implied_vol_bs2002_call(market_price, contract, divs=divs)
                if contract.is_call
                else implied_vol_bs2002_put(market_price, contract, divs=divs)
            )
            if ok_cpp and not math.isnan(iv_cpp):
                try:
                    cpp_divs = _to_cpp_divs(divs)
                    if contract.is_call:
                        price, _delta, _gamma, _theta, _vega, _rho = quantcore.bs2002_greeks_call(
                            contract.spot,
                            contract.strike,
                            contract.tau,
                            contract.rate,
                            iv_cpp,
                            cpp_divs,
                        )
                    else:
                        price, _delta, _gamma, _theta, _vega, _rho = quantcore.bs2002_greeks_put(
                            contract.spot,
                            contract.strike,
                            contract.tau,
                            contract.rate,
                            iv_cpp,
                            cpp_divs,
                        )
                    american_price = float(price)
                    european_price = price_euro_bs(
                        BSInput(
                            spot=contract.spot,
                            strike=contract.strike,
                            rate=contract.rate,
                            dividend=contract.dividend,
                            tau=contract.tau,
                            vol=iv_cpp,
                            is_call=contract.is_call,
                        )
                    ).price
                    eep = max(american_price - european_price, 0.0)
                    eep = min(eep, max(market_price * 0.98, 0.0))
                    return AmericanIVDiagnostics(
                        implied_vol=iv_cpp,
                        american_price=american_price,
                        european_price=european_price,
                        eep=eep,
                        success=True,
                    )
                except Exception:
                    pass

        iv, ok = implied_vol_american(
            market_price,
            contract,
            steps=self.steps,
            divs=divs,
            force_zero_q=force_zero_q,
        )
        if not ok or math.isnan(iv):
            return AmericanIVDiagnostics(
                implied_vol=float("nan"),
                american_price=market_price,
                european_price=market_price,
                eep=0.0,
                success=False,
            )

        american_price = american_binomial_price(
            contract,
            iv,
            steps=self.steps,
            divs=divs,
            force_zero_q=force_zero_q,
        )
        european_price = price_euro_bs(
            BSInput(
                spot=contract.spot,
                strike=contract.strike,
                rate=contract.rate,
                dividend=contract.dividend,
                tau=contract.tau,
                vol=iv,
                is_call=contract.is_call,
            )
        ).price
        eep = max(american_price - european_price, 0.0)
        eep = min(eep, max(market_price * 0.98, 0.0))
        return AmericanIVDiagnostics(
            implied_vol=iv,
            american_price=american_price,
            european_price=european_price,
            eep=eep,
            success=True,

        )


def estimate_luba_proxy_eep(
    market_price: float,
    option_type: str,
    spot: float,
    strike: float,
    tau: float,
    rate: float = 0.04,
    dividend: float = 0.0,
) -> float:
    if option_type == "call" and dividend <= 0.0:
        return 0.0

    intrinsic = max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
    if option_type == "call":
        boundary = terminal_boundary_call(strike, rate, dividend)
        if spot >= boundary:
            return max(market_price - intrinsic, 0.0)
    else:
        boundary = terminal_boundary_put(strike, rate, dividend)
        if spot <= boundary:
            return max(market_price - intrinsic, 0.0)

    moneyness = abs(math.log(max(spot, 1e-8) / max(strike, 1e-8)))
    root_tau = math.sqrt(max(tau, 1e-8))
    base = max(market_price, 0.0)
    premium = base * (0.006 + 0.016 * root_tau) + 0.015 * moneyness
    if option_type == "put":
        premium *= 1.10
    return max(0.0, min(premium, base * 0.95))


def estimate_luba_rim_eep(
    market_price: float,
    option_type: str,
    spot: float,
    strike: float,
    tau: float,
    rate: float,
    dividend: float,
    sigma: float,
    method: str = "luba_2pt",
    use_escrow: bool = False,
    divs: list[DividendEvent] | tuple[DividendEvent, ...] = (),
    rim_nodes: int = 100,
) -> float:
    eep, _diag = estimate_luba_rim_eep_with_diagnostics(
        market_price=market_price,
        option_type=option_type,
        spot=spot,
        strike=strike,
        tau=tau,
        rate=rate,
        dividend=dividend,
        sigma=sigma,
        method=method,
        use_escrow=use_escrow,
        divs=divs,
        rim_nodes=rim_nodes,
    )
    return eep


def estimate_luba_rim_eep_with_diagnostics(
    market_price: float,
    option_type: str,
    spot: float,
    strike: float,
    tau: float,
    rate: float,
    dividend: float,
    sigma: float,
    method: str = "luba_2pt",
    use_escrow: bool = False,
    divs: list[DividendEvent] | tuple[DividendEvent, ...] = (),
    rim_nodes: int = 100,
) -> tuple[float, LubaRimDiagnostics]:
    if tau <= 0.0 or market_price <= 0.0 or sigma <= 0.0:
        return 0.0, LubaRimDiagnostics(
            model_id=f"{method}_{option_type.lower()}",
            converged=False,
            iterations=0,
            sse_final=float("inf"),
            params={"nodes": float(max(16, rim_nodes))},
        )

    s_eff = escrowed_spot(spot, rate=rate, tau=tau, divs=divs) if use_escrow else spot
    q_eff = 0.0 if use_escrow else dividend
    is_call = option_type.lower() == "call"

    try:
        if method == "rim":
            american, diag = (
                calc_rim_call_with_diagnostics(s_eff, strike, tau, rate, q_eff, sigma, n=rim_nodes)
                if is_call
                else calc_rim_put_with_diagnostics(s_eff, strike, tau, rate, q_eff, sigma, n=rim_nodes)
            )
        else:
            american, diag = (
                calc_luba_2pt_call_with_diagnostics(s_eff, strike, tau, rate, q_eff, sigma)
                if is_call
                else calc_luba_2pt_put_with_diagnostics(s_eff, strike, tau, rate, q_eff, sigma)
            )
    except Exception:
        proxy = estimate_luba_proxy_eep(
            market_price=market_price,
            option_type=option_type,
            spot=spot,
            strike=strike,
            tau=tau,
            rate=rate,
            dividend=dividend,
        )
        return proxy, LubaRimDiagnostics(
            model_id=f"{method}_{option_type.lower()}",
            converged=False,
            iterations=0,
            sse_final=float("inf"),
            params={"nodes": float(max(16, rim_nodes))},
        )

    european = price_euro_bs(
        BSInput(
            spot=s_eff,
            strike=strike,
            rate=rate,
            dividend=q_eff,
            tau=tau,
            vol=sigma,
            is_call=is_call,
        )
    ).price
    eep = max(american - european, 0.0)
    bounded = max(0.0, min(eep, max(market_price * 0.98, 0.0)))
    return bounded, diag

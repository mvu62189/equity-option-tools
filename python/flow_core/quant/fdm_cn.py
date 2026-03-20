from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

from .conventions import theta_one_day_forward
from .dividends import DividendEvent
from .models import AmericanContract


@dataclass(slots=True)
class FDMCNGreeksResult:
    price: float
    delta: float
    gamma: float
    theta: float
    success: bool
    scheme: str = "log"
    comparison_price: float | None = None


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
        idx = int(round(t / dt))
        idx = max(1, min(steps - 1, idx))
        out[idx] = out.get(idx, 0.0) + amt
    return out


def _interp_linear(x: float, xs: np.ndarray, ys: np.ndarray) -> float:
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    idx = int(np.searchsorted(xs, x))
    x0, x1 = float(xs[idx - 1]), float(xs[idx])
    y0, y1 = float(ys[idx - 1]), float(ys[idx])
    if abs(x1 - x0) < 1e-12:
        return y0
    w = (x - x0) / (x1 - x0)
    return y0 + w * (y1 - y0)


def _thomas(lower: np.ndarray, diag: np.ndarray, upper: np.ndarray, rhs: np.ndarray) -> np.ndarray:
    n = diag.size
    c_prime = np.zeros(n - 1, dtype=float)
    d_prime = np.zeros(n, dtype=float)

    c_prime[0] = upper[0] / diag[0]
    d_prime[0] = rhs[0] / diag[0]

    for i in range(1, n - 1):
        den = diag[i] - lower[i - 1] * c_prime[i - 1]
        den = den if abs(den) > 1e-14 else 1e-14
        c_prime[i] = upper[i] / den
        d_prime[i] = (rhs[i] - lower[i - 1] * d_prime[i - 1]) / den

    den_last = diag[n - 1] - lower[n - 2] * c_prime[n - 2]
    den_last = den_last if abs(den_last) > 1e-14 else 1e-14
    d_prime[n - 1] = (rhs[n - 1] - lower[n - 2] * d_prime[n - 2]) / den_last

    x = np.zeros(n, dtype=float)
    x[n - 1] = d_prime[n - 1]
    for i in range(n - 2, -1, -1):
        x[i] = d_prime[i] - c_prime[i] * x[i + 1]
    return x


def _boundary_values_linear(contract: AmericanContract, s_max: float) -> tuple[float, float]:
    if contract.is_call:
        return 0.0, max(s_max - contract.strike, 0.0)
    return contract.strike, 0.0


def _solve_cn_grid_linear(
    contract: AmericanContract,
    vol: float,
    s_steps: int,
    t_steps: int,
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
    s_max_mult: float,
) -> tuple[np.ndarray, np.ndarray]:
    n_s = max(40, s_steps)
    n_t = max(40, t_steps)
    s_max = max(s_max_mult * contract.spot, 2.0 * contract.strike, contract.spot + contract.strike)
    d_s = s_max / n_s
    dt = contract.tau / n_t

    s_grid = np.linspace(0.0, s_max, n_s + 1)
    if contract.is_call:
        values = np.maximum(s_grid - contract.strike, 0.0)
    else:
        values = np.maximum(contract.strike - s_grid, 0.0)

    div_steps = _build_dividend_step_map(divs=divs, tau=contract.tau, steps=n_t)

    for n in range(n_t - 1, -1, -1):
        theta = 1.0 if (n_t - n) <= 2 else 0.5

        n_int = n_s - 1
        lower = np.zeros(n_int - 1, dtype=float)
        diag = np.zeros(n_int, dtype=float)
        upper = np.zeros(n_int - 1, dtype=float)
        rhs = np.zeros(n_int, dtype=float)

        left_bc, right_bc = _boundary_values_linear(contract, s_max)

        for i in range(n_int):
            j = i + 1
            s_j = s_grid[j]

            a = 0.5 * vol * vol * s_j * s_j / (d_s * d_s) - 0.5 * contract.rate * s_j / d_s
            b = -(vol * vol * s_j * s_j / (d_s * d_s) + contract.rate)
            c = 0.5 * vol * vol * s_j * s_j / (d_s * d_s) + 0.5 * contract.rate * s_j / d_s

            l = -dt * theta * a
            d = 1.0 - dt * theta * b
            u = -dt * theta * c

            rhs_i = values[j] + dt * (1.0 - theta) * (a * values[j - 1] + b * values[j] + c * values[j + 1])

            if i > 0:
                lower[i - 1] = l
            if i < n_int - 1:
                upper[i] = u
            diag[i] = d
            rhs[i] = rhs_i

            if i == 0:
                rhs[i] -= l * left_bc
            if i == n_int - 1:
                rhs[i] -= u * right_bc

        sol = _thomas(lower, diag, upper, rhs)

        new_values = values.copy()
        new_values[0] = left_bc
        new_values[-1] = right_bc
        new_values[1:-1] = sol

        jump = div_steps.get(n)
        if jump is not None and jump > 0.0:
            jumped = np.zeros_like(new_values)
            for j, s_pre in enumerate(s_grid):
                s_post = max(float(s_pre) - jump, 0.0)
                jumped[j] = _interp_linear(s_post, s_grid, new_values)
            new_values = jumped

        if contract.is_call:
            intrinsic = np.maximum(s_grid - contract.strike, 0.0)
        else:
            intrinsic = np.maximum(contract.strike - s_grid, 0.0)
        values = np.maximum(new_values, intrinsic)

    return s_grid, values


def _build_log_grid(contract: AmericanContract, vol: float, n_x: int, std_mult: float = 5.0) -> np.ndarray:
    n_x = max(60, n_x)
    if n_x % 2 == 0:
        n_x += 1

    x_k = math.log(max(contract.strike, 1e-12))
    x_s = math.log(max(contract.spot, 1e-12))
    std = max(vol * math.sqrt(max(contract.tau, 1e-8)), 1e-4)

    left_target = min(x_s, x_k) - std_mult * std
    right_target = max(x_s, x_k) + std_mult * std

    half = n_x // 2
    dx = max((x_k - left_target) / half, (right_target - x_k) / half, 1e-4)

    idx = np.arange(n_x, dtype=float)
    return x_k + (idx - half) * dx


def _boundary_values_log(contract: AmericanContract, s_min: float, s_max: float) -> tuple[float, float]:
    if contract.is_call:
        return 0.0, max(s_max - contract.strike, 0.0)
    # Discounted strike is more stable for put lower boundary in transformed PDE.
    return contract.strike * math.exp(-contract.rate * contract.tau), 0.0


def _solve_cn_grid_log(
    contract: AmericanContract,
    vol: float,
    x_steps: int,
    t_steps: int,
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
) -> tuple[np.ndarray, np.ndarray]:
    x_grid = _build_log_grid(contract, vol, n_x=max(60, x_steps))
    n_x = x_grid.size
    n_t = max(40, t_steps)
    dt = contract.tau / n_t
    dx = float(x_grid[1] - x_grid[0])

    s_grid = np.exp(x_grid)
    if contract.is_call:
        values = np.maximum(s_grid - contract.strike, 0.0)
    else:
        values = np.maximum(contract.strike - s_grid, 0.0)

    div_steps = _build_dividend_step_map(divs=divs, tau=contract.tau, steps=n_t)

    # Log-space PDE coefficients.
    a2 = 0.5 * vol * vol
    b1 = contract.rate - contract.dividend - 0.5 * vol * vol

    coeff_l = a2 / (dx * dx) - b1 / (2.0 * dx)
    coeff_d = -(2.0 * a2 / (dx * dx) + contract.rate)
    coeff_u = a2 / (dx * dx) + b1 / (2.0 * dx)

    for n in range(n_t - 1, -1, -1):
        theta = 1.0 if (n_t - n) <= 2 else 0.5

        n_int = n_x - 2
        lower = np.zeros(n_int - 1, dtype=float)
        diag = np.zeros(n_int, dtype=float)
        upper = np.zeros(n_int - 1, dtype=float)
        rhs = np.zeros(n_int, dtype=float)

        left_bc, right_bc = _boundary_values_log(contract, s_grid[0], s_grid[-1])

        for i in range(n_int):
            j = i + 1

            l = -dt * theta * coeff_l
            d = 1.0 - dt * theta * coeff_d
            u = -dt * theta * coeff_u

            rhs_i = values[j] + dt * (1.0 - theta) * (
                coeff_l * values[j - 1] + coeff_d * values[j] + coeff_u * values[j + 1]
            )

            if i > 0:
                lower[i - 1] = l
            if i < n_int - 1:
                upper[i] = u
            diag[i] = d
            rhs[i] = rhs_i

            if i == 0:
                rhs[i] -= l * left_bc
            if i == n_int - 1:
                rhs[i] -= u * right_bc

        sol = _thomas(lower, diag, upper, rhs)

        new_values = values.copy()
        new_values[0] = left_bc
        new_values[-1] = right_bc
        new_values[1:-1] = sol

        jump = div_steps.get(n)
        if jump is not None and jump > 0.0:
            jumped = np.zeros_like(new_values)
            for j, s_pre in enumerate(s_grid):
                s_post = max(float(s_pre) - jump, 0.0)
                jumped[j] = _interp_linear(s_post, s_grid, new_values)
            new_values = jumped

        if contract.is_call:
            intrinsic = np.maximum(s_grid - contract.strike, 0.0)
        else:
            intrinsic = np.maximum(contract.strike - s_grid, 0.0)
        values = np.maximum(new_values, intrinsic)

    return x_grid, values


def _extract_log_greeks(
    x_grid: np.ndarray,
    values: np.ndarray,
    spot: float,
) -> tuple[float, float, float]:
    x_spot = math.log(max(spot, 1e-12))
    price = _interp_linear(x_spot, x_grid, values)

    idx = int(np.searchsorted(x_grid, x_spot))
    idx = max(1, min(idx, len(x_grid) - 2))
    dx = float(x_grid[1] - x_grid[0])

    delta_x = (values[idx + 1] - values[idx - 1]) / (2.0 * dx)
    gamma_x = (values[idx + 1] - 2.0 * values[idx] + values[idx - 1]) / (dx * dx)

    s = max(spot, 1e-12)
    delta = delta_x / s
    gamma = (gamma_x - delta_x) / (s * s)
    return float(price), float(delta), float(gamma)


def _extract_linear_greeks(
    s_grid: np.ndarray,
    values: np.ndarray,
    spot: float,
) -> tuple[float, float, float]:
    price = _interp_linear(spot, s_grid, values)

    idx = int(np.searchsorted(s_grid, spot))
    idx = max(1, min(idx, len(s_grid) - 2))
    d_s = float(s_grid[1] - s_grid[0])

    delta = (values[idx + 1] - values[idx - 1]) / (2.0 * d_s)
    gamma = (values[idx + 1] - 2.0 * values[idx] + values[idx - 1]) / (d_s * d_s)
    return float(price), float(delta), float(gamma)


def _price_greeks_cn_single(
    contract: AmericanContract,
    vol: float,
    s_steps: int,
    t_steps: int,
    s_max_mult: float,
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
    scheme: Literal["log", "linear"],
) -> FDMCNGreeksResult:
    if scheme == "log":
        x_grid, values = _solve_cn_grid_log(
            contract=contract,
            vol=vol,
            x_steps=s_steps,
            t_steps=t_steps,
            divs=divs,
        )
        price, delta, gamma = _extract_log_greeks(x_grid, values, contract.spot)
    else:
        s_grid, values = _solve_cn_grid_linear(
            contract=contract,
            vol=vol,
            s_steps=s_steps,
            t_steps=t_steps,
            divs=divs,
            s_max_mult=s_max_mult,
        )
        price, delta, gamma = _extract_linear_greeks(s_grid, values, contract.spot)

    dt_day = 1.0 / 365.0
    tau_prev = max(contract.tau - dt_day, 1e-8)
    contract_prev = AmericanContract(
        spot=contract.spot,
        strike=contract.strike,
        rate=contract.rate,
        dividend=contract.dividend,
        tau=tau_prev,
        is_call=contract.is_call,
    )

    if scheme == "log":
        x_prev, v_prev = _solve_cn_grid_log(
            contract=contract_prev,
            vol=vol,
            x_steps=s_steps,
            t_steps=max(80, t_steps // 2),
            divs=divs,
        )
        price_prev, _d_prev, _g_prev = _extract_log_greeks(x_prev, v_prev, contract_prev.spot)
    else:
        s_prev, v_prev = _solve_cn_grid_linear(
            contract=contract_prev,
            vol=vol,
            s_steps=s_steps,
            t_steps=max(80, t_steps // 2),
            divs=divs,
            s_max_mult=s_max_mult,
        )
        price_prev, _d_prev, _g_prev = _extract_linear_greeks(s_prev, v_prev, contract_prev.spot)

    theta = theta_one_day_forward(value_t=price, value_t_minus_dt=price_prev)

    return FDMCNGreeksResult(
        price=float(price),
        delta=float(delta),
        gamma=float(gamma),
        theta=float(theta),
        success=True,
        scheme=scheme,
    )


def price_greeks_crank_nicolson(
    contract: AmericanContract,
    vol: float,
    s_steps: int = 220,
    t_steps: int = 220,
    s_max_mult: float = 3.0,
    divs: list[DividendEvent] | tuple[DividendEvent, ...] = (),
    scheme: Literal["log", "linear"] = "log",
    include_comparison: bool = False,
) -> FDMCNGreeksResult:
    if contract.spot <= 0.0 or contract.strike <= 0.0 or contract.tau <= 0.0 or vol <= 0.0:
        return FDMCNGreeksResult(float("nan"), float("nan"), float("nan"), float("nan"), False, scheme=scheme)

    try:
        out = _price_greeks_cn_single(
            contract=contract,
            vol=vol,
            s_steps=s_steps,
            t_steps=t_steps,
            s_max_mult=s_max_mult,
            divs=divs,
            scheme=scheme,
        )
        if include_comparison:
            alt_scheme: Literal["log", "linear"] = "linear" if scheme == "log" else "log"
            alt = _price_greeks_cn_single(
                contract=contract,
                vol=vol,
                s_steps=s_steps,
                t_steps=t_steps,
                s_max_mult=s_max_mult,
                divs=divs,
                scheme=alt_scheme,
            )
            out.comparison_price = alt.price
        return out
    except Exception:
        return FDMCNGreeksResult(float("nan"), float("nan"), float("nan"), float("nan"), False, scheme=scheme)

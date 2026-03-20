from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal, getcontext
from functools import lru_cache

import numpy as np
from scipy.optimize import brentq

from .bs import price_euro_bs
from .conventions import terminal_boundary_call, terminal_boundary_put
from .dividends import DividendEvent, escrowed_spot
from .models import BSInput


@dataclass(slots=True)
class LaplaceLeapsResult:
    price: float
    vega: float
    rho: float
    success: bool


def _characteristic_roots(r: float, q: float, sigma: float, lam: float) -> tuple[float, float]:
    sigma = max(sigma, 1e-8)
    a = 0.5 * sigma * sigma
    b = r - q - 0.5 * sigma * sigma
    c = -(r + lam)
    disc = max(b * b - 4.0 * a * c, 0.0)
    sqrt_disc = math.sqrt(disc)
    rho1 = (-b + sqrt_disc) / (2.0 * a)
    rho2 = (-b - sqrt_disc) / (2.0 * a)
    return rho1, rho2


@lru_cache(maxsize=8)
def _stehfest_weights(m: int) -> tuple[float, ...]:
    if m % 2 != 0:
        raise ValueError("Stehfest M must be even")

    half = m // 2
    getcontext().prec = 80
    weights: list[float] = [0.0] * (m + 1)

    for k in range(1, m + 1):
        s = Decimal(0)
        j_lo = (k + 1) // 2
        j_hi = min(k, half)
        for j in range(j_lo, j_hi + 1):
            num = (Decimal(j) ** half) * Decimal(math.factorial(2 * j))
            den = (
                Decimal(math.factorial(half - j))
                * Decimal(math.factorial(j))
                * Decimal(math.factorial(j - 1))
                * Decimal(math.factorial(k - j))
                * Decimal(math.factorial(2 * j - k))
            )
            s += num / den
        if (k + half) % 2 == 1:
            s = -s
        weights[k] = float(s)

    return tuple(weights)


@lru_cache(maxsize=8)
def _laguerre_nodes_weights(n: int) -> tuple[np.ndarray, np.ndarray]:
    x, w = np.polynomial.laguerre.laggauss(max(8, n))
    return x.astype(float), w.astype(float)


def _hat_c_eur_laplace(
    s: float,
    k: float,
    r: float,
    q: float,
    sigma: float,
    lam: float,
    quad_n: int = 24,
) -> float:
    lam = max(lam, 1e-8)
    inv_lam = 1.0 / lam
    x, w = _laguerre_nodes_weights(quad_n)
    acc = 0.0
    for xx, ww in zip(x, w, strict=True):
        tau = max(xx * inv_lam, 1e-8)
        c = price_euro_bs(
            BSInput(
                spot=s,
                strike=k,
                rate=r,
                dividend=q,
                tau=tau,
                vol=sigma,
                is_call=True,
            )
        ).price
        acc += float(ww) * c
    return acc * inv_lam


def _hat_p_eur_laplace(
    s: float,
    k: float,
    r: float,
    q: float,
    sigma: float,
    lam: float,
) -> float:
    lam = max(lam, 1e-8)
    s = max(s, 1e-12)
    k = max(k, 1e-12)
    rho1, rho2 = _characteristic_roots(r, q, sigma, lam)
    c1, c2 = _put_laplace_constants(k, r, q, lam, rho1, rho2)
    q_lam = _safe_nonzero(q + lam)
    r_lam = _safe_nonzero(r + lam)
    if s <= k:
        return c1 * (s**rho1) + (k / r_lam) - (s / q_lam)
    return c2 * (s**rho2)


def _d_hat_p_eur_laplace_d_s(
    s: float,
    k: float,
    r: float,
    q: float,
    sigma: float,
    lam: float,
) -> float:
    lam = max(lam, 1e-8)
    s = max(s, 1e-12)
    k = max(k, 1e-12)
    rho1, rho2 = _characteristic_roots(r, q, sigma, lam)
    c1, c2 = _put_laplace_constants(k, r, q, lam, rho1, rho2)
    q_lam = _safe_nonzero(q + lam)
    if s <= k:
        return c1 * rho1 * (s ** (rho1 - 1.0)) - (1.0 / q_lam)
    return c2 * rho2 * (s ** (rho2 - 1.0))


def _safe_nonzero(x: float, eps: float = 1e-12) -> float:
    if abs(x) >= eps:
        return x
    return eps if x >= 0.0 else -eps


def _put_laplace_constants(
    k: float,
    r: float,
    q: float,
    lam: float,
    rho1: float,
    rho2: float,
) -> tuple[float, float]:
    k = max(k, 1e-12)
    denom = _safe_nonzero(rho1 - rho2)
    q_lam = _safe_nonzero(q + lam)
    r_lam = _safe_nonzero(r + lam)
    c1 = (k ** (1.0 - rho1)) / denom * (((1.0 - rho2) / q_lam) + (rho2 / r_lam))
    c2 = (k ** (1.0 - rho2)) / denom * (((1.0 - rho1) / q_lam) + (rho1 / r_lam))
    return c1, c2


def _solve_b_star_lambda(
    k: float,
    r: float,
    q: float,
    sigma: float,
    lam: float,
    rho2: float,
) -> float:
    if q <= 0.0:
        return terminal_boundary_call(k, r, q)

    denom = -rho2 if abs(rho2) > 1e-10 else 1e-10

    def f_root(b: float) -> float:
        hat_eur = _hat_c_eur_laplace(b, k, r, q, sigma, lam)
        return hat_eur + b / denom - (b - k) / lam

    lo = max(k * (1.0 + 1e-8), 1e-8)
    hi = max(8.0 * k, 4.0 * lo)

    f_lo = f_root(lo)
    f_hi = f_root(hi)
    for _ in range(6):
        if math.isfinite(f_lo) and math.isfinite(f_hi) and f_lo * f_hi <= 0.0:
            break
        hi *= 1.5
        f_hi = f_root(hi)

    if math.isfinite(f_lo) and math.isfinite(f_hi) and f_lo * f_hi <= 0.0:
        try:
            return float(brentq(f_root, lo, hi, maxiter=120, xtol=1e-9))
        except Exception:
            pass

    fallback = terminal_boundary_call(k, r, q)
    if math.isfinite(fallback) and fallback > 0.0:
        return fallback
    return k


def _solve_b_put_star_lambda(
    k: float,
    r: float,
    q: float,
    sigma: float,
    lam: float,
    rho1: float,
) -> float:
    denom = rho1 if abs(rho1) > 1e-10 else 1e-10
    lam = max(lam, 1e-8)
    k = max(k, 1e-12)
    c1, _ = _put_laplace_constants(
        k=k,
        r=r,
        q=q,
        lam=lam,
        rho1=rho1,
        rho2=_characteristic_roots(r, q, sigma, lam)[1],
    )
    q_lam = _safe_nonzero(q + lam)
    r_lam = _safe_nonzero(r + lam)

    def f_root(b: float) -> float:
        # Put early-exercise boundary satisfies B* <= K.
        b = min(max(b, 1e-12), k)
        hat_eur = c1 * (b**rho1) + (k / r_lam) - (b / q_lam)
        return hat_eur + b / denom - (k - b) / lam

    lo = 1e-8
    hi = max(k * (1.0 - 1e-8), 1e-6)

    f_lo = f_root(lo)
    f_hi = f_root(hi)
    for _ in range(6):
        if math.isfinite(f_lo) and math.isfinite(f_hi) and f_lo * f_hi <= 0.0:
            break
        lo = max(lo * 0.5, 1e-10)
        hi = max(hi * 0.97, lo + 1e-8)
        f_lo = f_root(lo)
        f_hi = f_root(hi)

    if math.isfinite(f_lo) and math.isfinite(f_hi) and f_lo * f_hi <= 0.0:
        try:
            return float(brentq(f_root, lo, hi, maxiter=120, xtol=1e-9))
        except Exception:
            pass

    fallback = terminal_boundary_put(k, r, q)
    if math.isfinite(fallback) and fallback > 0.0:
        return fallback
    return min(k, max(1e-8, 0.75 * k))


def _hat_c_american_zhu(
    s: float,
    k: float,
    r: float,
    q: float,
    sigma: float,
    lam: float,
) -> float:
    rho1, rho2 = _characteristic_roots(r, q, sigma, lam)
    hat_eur = _hat_c_eur_laplace(s, k, r, q, sigma, lam)
    if q <= 0.0:
        return hat_eur

    b_star = _solve_b_star_lambda(k, r, q, sigma, lam, rho2)
    if not math.isfinite(b_star) or b_star <= 0.0:
        return hat_eur

    denom = -rho2 if abs(rho2) > 1e-10 else 1e-10
    premium = (b_star / denom) * ((max(s, 1e-12) / b_star) ** rho1)
    return hat_eur + premium


def _hat_p_american_zhu(
    s: float,
    k: float,
    r: float,
    q: float,
    sigma: float,
    lam: float,
) -> float:
    rho1, rho2 = _characteristic_roots(r, q, sigma, lam)
    hat_eur = _hat_p_eur_laplace(s, k, r, q, sigma, lam)
    b_star = _solve_b_put_star_lambda(k, r, q, sigma, lam, rho1)
    if not math.isfinite(b_star) or b_star <= 0.0:
        return hat_eur
    premium = (b_star / max(rho1, 1e-10)) * ((max(s, 1e-12) / b_star) ** rho2)
    return hat_eur + premium


def price_laplace_zhu_call(
    s: float,
    k: float,
    tau: float,
    r: float,
    q: float,
    sigma: float,
    m: int = 12,
) -> float:
    if tau <= 0.0:
        return max(s - k, 0.0)
    if m % 2 != 0:
        raise ValueError("Stehfest M must be even")

    w = _stehfest_weights(m)
    ln2 = math.log(2.0)
    acc = 0.0
    for i in range(1, m + 1):
        lam = i * ln2 / tau
        acc += w[i] * _hat_c_american_zhu(s, k, r, q, sigma, lam)

    value = ln2 / tau * acc
    return max(value, 0.0)


def price_laplace_zhu_put(
    s: float,
    k: float,
    tau: float,
    r: float,
    q: float,
    sigma: float,
    m: int = 12,
) -> float:
    if tau <= 0.0:
        return max(k - s, 0.0)
    if m % 2 != 0:
        raise ValueError("Stehfest M must be even")

    w = _stehfest_weights(m)
    ln2 = math.log(2.0)
    acc = 0.0
    for i in range(1, m + 1):
        lam = i * ln2 / tau
        acc += w[i] * _hat_p_american_zhu(s, k, r, q, sigma, lam)

    value = ln2 / tau * acc
    return max(value, 0.0)


def price_laplace_zhu_call_escrowed(
    s: float,
    k: float,
    tau: float,
    r: float,
    sigma: float,
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
    m: int = 12,
) -> float:
    s_eff = escrowed_spot(s, rate=r, tau=tau, divs=divs)
    return price_laplace_zhu_call(s_eff, k, tau, r, q=0.0, sigma=sigma, m=m)


def price_laplace_zhu_put_escrowed(
    s: float,
    k: float,
    tau: float,
    r: float,
    sigma: float,
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
    m: int = 12,
) -> float:
    s_eff = escrowed_spot(s, rate=r, tau=tau, divs=divs)
    return price_laplace_zhu_put(s_eff, k, tau, r, q=0.0, sigma=sigma, m=m)


def price_vega_rho_laplace_zhu_call(
    s: float,
    k: float,
    tau: float,
    r: float,
    q: float,
    sigma: float,
    m: int = 12,
) -> LaplaceLeapsResult:
    try:
        base = price_laplace_zhu_call(s, k, tau, r, q, sigma, m=m)
        ds = 1e-3
        dr = 1e-4
        up_sigma = price_laplace_zhu_call(s, k, tau, r, q, sigma + ds, m=m)
        dn_sigma = price_laplace_zhu_call(s, k, tau, r, q, max(sigma - ds, 1e-4), m=m)
        up_r = price_laplace_zhu_call(s, k, tau, r + dr, q, sigma, m=m)
        dn_r = price_laplace_zhu_call(s, k, tau, r - dr, q, sigma, m=m)
        vega = (up_sigma - dn_sigma) / (2.0 * ds)
        rho = (up_r - dn_r) / (2.0 * dr)
        return LaplaceLeapsResult(price=base, vega=vega, rho=rho, success=True)
    except Exception:
        return LaplaceLeapsResult(price=float("nan"), vega=float("nan"), rho=float("nan"), success=False)


def price_vega_rho_laplace_zhu_put(
    s: float,
    k: float,
    tau: float,
    r: float,
    q: float,
    sigma: float,
    m: int = 12,
) -> LaplaceLeapsResult:
    try:
        base = price_laplace_zhu_put(s, k, tau, r, q, sigma, m=m)
        ds = 1e-3
        dr = 1e-4
        up_sigma = price_laplace_zhu_put(s, k, tau, r, q, sigma + ds, m=m)
        dn_sigma = price_laplace_zhu_put(s, k, tau, r, q, max(sigma - ds, 1e-4), m=m)
        up_r = price_laplace_zhu_put(s, k, tau, r + dr, q, sigma, m=m)
        dn_r = price_laplace_zhu_put(s, k, tau, r - dr, q, sigma, m=m)
        vega = (up_sigma - dn_sigma) / (2.0 * ds)
        rho = (up_r - dn_r) / (2.0 * dr)
        return LaplaceLeapsResult(price=base, vega=vega, rho=rho, success=True)
    except Exception:
        return LaplaceLeapsResult(price=float("nan"), vega=float("nan"), rho=float("nan"), success=False)

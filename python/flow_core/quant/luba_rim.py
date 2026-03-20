from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq, newton

from .bs import price_euro_bs
from .conventions import terminal_boundary_call, terminal_boundary_put
from .dividends import DividendEvent, escrowed_spot
from .models import BSInput


@dataclass(slots=True)
class LubaRimPrice:
    american: float
    european: float
    eep: float
    success: bool


@dataclass(slots=True)
class RootSolveInfo:
    root: float
    converged: bool
    iterations: int
    residual: float


@dataclass(slots=True)
class LubaRimDiagnostics:
    model_id: str
    converged: bool
    iterations: int
    sse_final: float
    params: dict[str, float]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _safe_d1_d2(s: float, b_u: float, dt: float, r: float, q: float, sigma: float) -> tuple[float, float]:
    dt = max(dt, 1e-12)
    sigma = max(sigma, 1e-8)
    b_u = max(b_u, 1e-12)
    s = max(s, 1e-12)
    d1 = (math.log(s / b_u) + (r - q + 0.5 * sigma * sigma) * dt) / (sigma * math.sqrt(dt))
    d2 = d1 - sigma * math.sqrt(dt)
    return d1, d2


def _call_integrand(s: float, b_u: float, dt: float, r: float, q: float, sigma: float, k: float) -> float:
    d1, d2 = _safe_d1_d2(s, b_u, dt, r, q, sigma)
    return q * s * math.exp(-q * dt) * _norm_cdf(d1) - r * k * math.exp(-r * dt) * _norm_cdf(d2)


def _put_integrand(s: float, b_u: float, dt: float, r: float, q: float, sigma: float, k: float) -> float:
    d1, d2 = _safe_d1_d2(s, b_u, dt, r, q, sigma)
    return r * k * math.exp(-r * dt) * _norm_cdf(-d2) - q * s * math.exp(-q * dt) * _norm_cdf(-d1)


def _gauss_legendre_interval(a: float, b: float, n: int) -> tuple[np.ndarray, np.ndarray]:
    x, w = np.polynomial.legendre.leggauss(max(8, n))
    u = 0.5 * (b - a) * x + 0.5 * (b + a)
    weights = 0.5 * (b - a) * w
    return u, weights


def _beta_roots(r: float, q: float, sigma: float) -> tuple[float, float]:
    a = 0.5 * sigma * sigma
    b = r - q - 0.5 * sigma * sigma
    c = -r
    disc = b * b - 4.0 * a * c
    disc = max(disc, 0.0)
    sqrt_disc = math.sqrt(disc)
    x1 = (-b + sqrt_disc) / (2.0 * a)
    x2 = (-b - sqrt_disc) / (2.0 * a)
    return x1, x2


def _euro_price(s: float, k: float, tau: float, r: float, q: float, sigma: float, is_call: bool) -> float:
    return price_euro_bs(
        BSInput(
            spot=s,
            strike=k,
            rate=r,
            dividend=q,
            tau=tau,
            vol=sigma,
            is_call=is_call,
        )
    ).price


def _bracket_for_root(f, lo: float, hi: float) -> tuple[float, float] | None:
    f_lo = f(lo)
    f_hi = f(hi)
    if math.isnan(f_lo) or math.isnan(f_hi):
        return None
    if f_lo * f_hi <= 0.0:
        return lo, hi
    span_lo, span_hi = lo, hi
    for _ in range(8):
        span_lo = max(span_lo * 0.7, 1e-8)
        span_hi = span_hi * 1.5
        f_lo = f(span_lo)
        f_hi = f(span_hi)
        if math.isnan(f_lo) or math.isnan(f_hi):
            continue
        if f_lo * f_hi <= 0.0:
            return span_lo, span_hi
    return None


def _solve_scalar_root_info(func, x0: float, lo: float, hi: float) -> RootSolveInfo:
    best_root = float(x0)
    try:
        best_residual = abs(float(func(best_root)))
    except Exception:
        best_residual = float("inf")
    iterations = 0

    try:
        root_newton, info_newton = newton(func, x0=x0, maxiter=60, tol=1e-8, full_output=True, disp=False)
        root_newton = float(root_newton)
        res_newton = abs(float(func(root_newton)))
        iterations += int(getattr(info_newton, "iterations", 0))
        if math.isfinite(res_newton) and (not math.isfinite(best_residual) or res_newton < best_residual):
            best_root = root_newton
            best_residual = res_newton
        if bool(getattr(info_newton, "converged", False)) and math.isfinite(root_newton):
            return RootSolveInfo(
                root=root_newton,
                converged=True,
                iterations=max(iterations, 1),
                residual=res_newton,
            )
    except Exception:
        pass

    bracket = _bracket_for_root(func, lo, hi)
    if bracket is not None:
        a, b = bracket
        try:
            root_brent, info_brent = brentq(func, a, b, maxiter=200, xtol=1e-10, full_output=True, disp=False)
            root_brent = float(root_brent)
            res_brent = abs(float(func(root_brent)))
            iterations += int(getattr(info_brent, "iterations", 0))
            if math.isfinite(res_brent) and (not math.isfinite(best_residual) or res_brent < best_residual):
                best_root = root_brent
                best_residual = res_brent
            if bool(getattr(info_brent, "converged", False)) and math.isfinite(root_brent):
                return RootSolveInfo(
                    root=root_brent,
                    converged=True,
                    iterations=max(iterations, 1),
                    residual=res_brent,
                )
        except Exception:
            pass

    return RootSolveInfo(
        root=best_root,
        converged=False,
        iterations=max(iterations, 1),
        residual=best_residual if math.isfinite(best_residual) else float("inf"),
    )


def _solve_scalar_root(func, x0: float, lo: float, hi: float) -> float:
    return _solve_scalar_root_info(func, x0=x0, lo=lo, hi=hi).root


def calc_luba_2pt_call_with_diagnostics(
    s: float,
    k: float,
    t: float,
    r: float,
    q: float,
    sigma: float,
    quad_n: int = 48,
) -> tuple[float, LubaRimDiagnostics]:
    if t <= 0.0:
        intrinsic = max(s - k, 0.0)
        return intrinsic, LubaRimDiagnostics(
            model_id="luba_2pt_call",
            converged=True,
            iterations=0,
            sse_final=0.0,
            params={"gamma": 0.0, "boundary_t": k, "boundary_inf": k, "quad_n": float(max(8, quad_n))},
        )
    if sigma <= 0.0:
        fallback = max(s - k * math.exp(-r * t), 0.0)
        return fallback, LubaRimDiagnostics(
            model_id="luba_2pt_call",
            converged=False,
            iterations=0,
            sse_final=float("inf"),
            params={"gamma": 0.0, "boundary_t": k, "boundary_inf": k, "quad_n": float(max(8, quad_n))},
        )
    if q <= 0.0:
        european = _euro_price(s, k, t, r, q, sigma, is_call=True)
        return european, LubaRimDiagnostics(
            model_id="luba_2pt_call",
            converged=True,
            iterations=0,
            sse_final=0.0,
            params={"gamma": 0.0, "boundary_t": k, "boundary_inf": k, "quad_n": float(max(8, quad_n))},
        )

    b_t = terminal_boundary_call(k, r, q)
    beta_pos, _beta_neg = _beta_roots(r, q, sigma)
    if beta_pos <= 1.0:
        beta_pos = 1.0001
    b_inf = (r / q) * k * beta_pos / (beta_pos - 1.0)
    b_inf = max(b_inf, b_t + 1e-8)

    def b_hat(u: float, gamma: float) -> float:
        return b_inf + (b_t - b_inf) * math.exp(-gamma * (t - u))

    t_mid = 0.5 * t

    def objective_gamma(gamma: float) -> float:
        b_mid = b_hat(t_mid, gamma)
        euro = _euro_price(b_mid, k, t - t_mid, r, q, sigma, is_call=True)
        u, w = _gauss_legendre_interval(t_mid, t, quad_n)
        integ = 0.0
        for uu, ww in zip(u, w, strict=True):
            dt = uu - t_mid
            integ += ww * _call_integrand(b_mid, b_hat(uu, gamma), dt, r, q, sigma, k)
        return b_mid - k - euro - integ

    root_info = _solve_scalar_root_info(objective_gamma, x0=1.0, lo=1e-6, hi=40.0)
    gamma = root_info.root

    euro = _euro_price(s, k, t, r, q, sigma, is_call=True)
    u, w = _gauss_legendre_interval(0.0, t, quad_n)
    eep = 0.0
    for uu, ww in zip(u, w, strict=True):
        eep += ww * _call_integrand(s, b_hat(uu, gamma), uu, r, q, sigma, k)
    american = euro + eep
    diagnostics = LubaRimDiagnostics(
        model_id="luba_2pt_call",
        converged=root_info.converged,
        iterations=root_info.iterations,
        sse_final=float(root_info.residual * root_info.residual),
        params={
            "gamma": float(gamma),
            "boundary_t": float(b_t),
            "boundary_inf": float(b_inf),
            "quad_n": float(max(8, quad_n)),
        },
    )
    return american, diagnostics


def calc_luba_2pt_call(
    s: float,
    k: float,
    t: float,
    r: float,
    q: float,
    sigma: float,
    quad_n: int = 48,
) -> float:
    price, _diag = calc_luba_2pt_call_with_diagnostics(
        s=s,
        k=k,
        t=t,
        r=r,
        q=q,
        sigma=sigma,
        quad_n=quad_n,
    )
    return price


def calc_luba_2pt_put_with_diagnostics(
    s: float,
    k: float,
    t: float,
    r: float,
    q: float,
    sigma: float,
    quad_n: int = 48,
) -> tuple[float, LubaRimDiagnostics]:
    if t <= 0.0:
        intrinsic = max(k - s, 0.0)
        return intrinsic, LubaRimDiagnostics(
            model_id="luba_2pt_put",
            converged=True,
            iterations=0,
            sse_final=0.0,
            params={"gamma": 0.0, "boundary_t": k, "boundary_inf": k, "quad_n": float(max(8, quad_n))},
        )
    if sigma <= 0.0:
        fallback = max(k * math.exp(-r * t) - s, 0.0)
        return fallback, LubaRimDiagnostics(
            model_id="luba_2pt_put",
            converged=False,
            iterations=0,
            sse_final=float("inf"),
            params={"gamma": 0.0, "boundary_t": k, "boundary_inf": k, "quad_n": float(max(8, quad_n))},
        )

    b_t = terminal_boundary_put(k, r, q)
    _beta_pos, beta_neg = _beta_roots(r, q, sigma)
    if beta_neg >= 0.0:
        beta_neg = -1e-4
    b_inf = k * beta_neg / (beta_neg - 1.0)
    b_inf = min(b_inf, b_t - 1e-8)

    def b_hat(u: float, gamma: float) -> float:
        return b_inf + (b_t - b_inf) * math.exp(-gamma * (t - u))

    t_mid = 0.5 * t

    def objective_gamma(gamma: float) -> float:
        b_mid = b_hat(t_mid, gamma)
        euro = _euro_price(b_mid, k, t - t_mid, r, q, sigma, is_call=False)
        u, w = _gauss_legendre_interval(t_mid, t, quad_n)
        integ = 0.0
        for uu, ww in zip(u, w, strict=True):
            dt = uu - t_mid
            integ += ww * _put_integrand(b_mid, b_hat(uu, gamma), dt, r, q, sigma, k)
        return k - b_mid - euro - integ

    root_info = _solve_scalar_root_info(objective_gamma, x0=1.0, lo=1e-6, hi=40.0)
    gamma = root_info.root

    euro = _euro_price(s, k, t, r, q, sigma, is_call=False)
    u, w = _gauss_legendre_interval(0.0, t, quad_n)
    eep = 0.0
    for uu, ww in zip(u, w, strict=True):
        eep += ww * _put_integrand(s, b_hat(uu, gamma), uu, r, q, sigma, k)
    american = euro + eep
    diagnostics = LubaRimDiagnostics(
        model_id="luba_2pt_put",
        converged=root_info.converged,
        iterations=root_info.iterations,
        sse_final=float(root_info.residual * root_info.residual),
        params={
            "gamma": float(gamma),
            "boundary_t": float(b_t),
            "boundary_inf": float(b_inf),
            "quad_n": float(max(8, quad_n)),
        },
    )
    return american, diagnostics


def calc_luba_2pt_put(
    s: float,
    k: float,
    t: float,
    r: float,
    q: float,
    sigma: float,
    quad_n: int = 48,
) -> float:
    price, _diag = calc_luba_2pt_put_with_diagnostics(
        s=s,
        k=k,
        t=t,
        r=r,
        q=q,
        sigma=sigma,
        quad_n=quad_n,
    )
    return price


def calc_rim_call_with_diagnostics(
    s: float,
    k: float,
    t: float,
    r: float,
    q: float,
    sigma: float,
    n: int = 100,
) -> tuple[float, LubaRimDiagnostics]:
    if t <= 0.0:
        intrinsic = max(s - k, 0.0)
        return intrinsic, LubaRimDiagnostics(
            model_id="rim_call",
            converged=True,
            iterations=0,
            sse_final=0.0,
            params={"nodes": float(max(16, n))},
        )
    if sigma <= 0.0:
        fallback = max(s - k * math.exp(-r * t), 0.0)
        return fallback, LubaRimDiagnostics(
            model_id="rim_call",
            converged=False,
            iterations=0,
            sse_final=float("inf"),
            params={"nodes": float(max(16, n))},
        )
    if q <= 0.0:
        european = _euro_price(s, k, t, r, q, sigma, is_call=True)
        return european, LubaRimDiagnostics(
            model_id="rim_call",
            converged=True,
            iterations=0,
            sse_final=0.0,
            params={"nodes": float(max(16, n))},
        )

    u, w = _gauss_legendre_interval(0.0, t, max(16, n))
    m = len(u)
    b = np.zeros(m, dtype=float)
    b[-1] = terminal_boundary_call(k, r, q)
    total_iterations = 0
    sse_total = 0.0
    converged = True

    for i in range(m - 2, -1, -1):
        ui = float(u[i])
        tau_i = max(t - ui, 1e-12)

        def f_root(bi: float) -> float:
            euro = _euro_price(bi, k, tau_i, r, q, sigma, is_call=True)
            ssum = 0.0
            for j in range(i + 1, m):
                dt = float(u[j] - ui)
                ssum += float(w[j]) * _call_integrand(bi, float(b[j]), dt, r, q, sigma, k)
            return bi - k - euro - ssum

        guess = max(b[i + 1], k)
        root_info = _solve_scalar_root_info(f_root, x0=guess, lo=max(0.2 * k, 1e-6), hi=max(5.0 * k, 2.0 * guess))
        b[i] = root_info.root
        total_iterations += root_info.iterations
        converged = converged and root_info.converged
        if math.isfinite(root_info.residual):
            sse_total += root_info.residual * root_info.residual
        else:
            converged = False

    euro = _euro_price(s, k, t, r, q, sigma, is_call=True)
    eep = 0.0
    for j in range(m):
        eep += float(w[j]) * _call_integrand(s, float(b[j]), float(u[j]), r, q, sigma, k)
    american = euro + eep
    diagnostics = LubaRimDiagnostics(
        model_id="rim_call",
        converged=converged,
        iterations=total_iterations,
        sse_final=(sse_total / max(m - 1, 1)),
        params={"nodes": float(m)},
    )
    return american, diagnostics


def calc_rim_call(
    s: float,
    k: float,
    t: float,
    r: float,
    q: float,
    sigma: float,
    n: int = 100,
) -> float:
    price, _diag = calc_rim_call_with_diagnostics(
        s=s,
        k=k,
        t=t,
        r=r,
        q=q,
        sigma=sigma,
        n=n,
    )
    return price


def calc_rim_put_with_diagnostics(
    s: float,
    k: float,
    t: float,
    r: float,
    q: float,
    sigma: float,
    n: int = 100,
) -> tuple[float, LubaRimDiagnostics]:
    if t <= 0.0:
        intrinsic = max(k - s, 0.0)
        return intrinsic, LubaRimDiagnostics(
            model_id="rim_put",
            converged=True,
            iterations=0,
            sse_final=0.0,
            params={"nodes": float(max(16, n))},
        )
    if sigma <= 0.0:
        fallback = max(k * math.exp(-r * t) - s, 0.0)
        return fallback, LubaRimDiagnostics(
            model_id="rim_put",
            converged=False,
            iterations=0,
            sse_final=float("inf"),
            params={"nodes": float(max(16, n))},
        )

    u, w = _gauss_legendre_interval(0.0, t, max(16, n))
    m = len(u)
    b = np.zeros(m, dtype=float)
    b[-1] = terminal_boundary_put(k, r, q)
    total_iterations = 0
    sse_total = 0.0
    converged = True

    for i in range(m - 2, -1, -1):
        ui = float(u[i])
        tau_i = max(t - ui, 1e-12)

        def f_root(bi: float) -> float:
            euro = _euro_price(bi, k, tau_i, r, q, sigma, is_call=False)
            ssum = 0.0
            for j in range(i + 1, m):
                dt = float(u[j] - ui)
                ssum += float(w[j]) * _put_integrand(bi, float(b[j]), dt, r, q, sigma, k)
            return k - bi - euro - ssum

        guess = min(max(b[i + 1], 1e-8), k)
        root_info = _solve_scalar_root_info(f_root, x0=guess, lo=1e-8, hi=max(2.0 * k, 2.0 * b[i + 1]))
        b[i] = root_info.root
        total_iterations += root_info.iterations
        converged = converged and root_info.converged
        if math.isfinite(root_info.residual):
            sse_total += root_info.residual * root_info.residual
        else:
            converged = False

    euro = _euro_price(s, k, t, r, q, sigma, is_call=False)
    eep = 0.0
    for j in range(m):
        eep += float(w[j]) * _put_integrand(s, float(b[j]), float(u[j]), r, q, sigma, k)
    american = euro + eep
    diagnostics = LubaRimDiagnostics(
        model_id="rim_put",
        converged=converged,
        iterations=total_iterations,
        sse_final=(sse_total / max(m - 1, 1)),
        params={"nodes": float(m)},
    )
    return american, diagnostics


def calc_rim_put(
    s: float,
    k: float,
    t: float,
    r: float,
    q: float,
    sigma: float,
    n: int = 100,
) -> float:
    price, _diag = calc_rim_put_with_diagnostics(
        s=s,
        k=k,
        t=t,
        r=r,
        q=q,
        sigma=sigma,
        n=n,
    )
    return price


def calc_luba_2pt_call_escrowed(
    s: float,
    k: float,
    t: float,
    r: float,
    sigma: float,
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
    quad_n: int = 48,
) -> float:
    s_eff = escrowed_spot(s, rate=r, tau=t, divs=divs)
    return calc_luba_2pt_call(s_eff, k, t, r, 0.0, sigma, quad_n=quad_n)


def calc_luba_2pt_put_escrowed(
    s: float,
    k: float,
    t: float,
    r: float,
    sigma: float,
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
    quad_n: int = 48,
) -> float:
    s_eff = escrowed_spot(s, rate=r, tau=t, divs=divs)
    return calc_luba_2pt_put(s_eff, k, t, r, 0.0, sigma, quad_n=quad_n)


def calc_rim_call_escrowed(
    s: float,
    k: float,
    t: float,
    r: float,
    sigma: float,
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
    n: int = 100,
) -> float:
    s_eff = escrowed_spot(s, rate=r, tau=t, divs=divs)
    return calc_rim_call(s_eff, k, t, r, 0.0, sigma, n=n)


def calc_rim_put_escrowed(
    s: float,
    k: float,
    t: float,
    r: float,
    sigma: float,
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
    n: int = 100,
) -> float:
    s_eff = escrowed_spot(s, rate=r, tau=t, divs=divs)
    return calc_rim_put(s_eff, k, t, r, 0.0, sigma, n=n)

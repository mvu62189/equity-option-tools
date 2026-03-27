from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal

import numpy as np
import polars as pl
from scipy.optimize import least_squares

from .models import SSVIConstraints, SSVIResult

try:  # optional compiled backend
    import quantcore  # type: ignore
except Exception:  # pragma: no cover
    quantcore = None


DEFAULT_INIT = np.array([0.01, 0.10, -0.2, 0.0, 0.25], dtype=float)
FitSpace = Literal["log", "strike"]


def _infer_tau(chain_frame: pl.DataFrame) -> float:
    if "expiration" not in chain_frame.columns:
        return 7.0 / 365.0

    try:
        expiration = chain_frame["expiration"][0]
        if hasattr(expiration, "year"):
            exp_dt = datetime(expiration.year, expiration.month, expiration.day, 16, 0, 0, tzinfo=timezone.utc)
        else:
            return 7.0 / 365.0

        if "asof_ts" in chain_frame.columns:
            asof = chain_frame["asof_ts"][0]
            if isinstance(asof, datetime):
                asof_dt = asof if asof.tzinfo is not None else asof.replace(tzinfo=timezone.utc)
            else:
                asof_dt = datetime.now(timezone.utc)
        else:
            asof_dt = datetime.now(timezone.utc)

        seconds = max((exp_dt - asof_dt).total_seconds(), 60.0)
        return max(seconds / (365.25 * 24.0 * 3600.0), 1.0 / 365.0)
    except Exception:
        return 7.0 / 365.0


def _compute_weights(chain_frame: pl.DataFrame, *, weight_col: str | None = None) -> np.ndarray:
    n = chain_frame.height
    if n == 0:
        return np.array([], dtype=float)

    if weight_col and weight_col in chain_frame.columns:
        weights = chain_frame[weight_col].to_numpy().astype(float)
        weights = np.where(np.isfinite(weights) & (weights > 0.0), weights, 0.0)
        w_sum = float(np.sum(weights))
        if w_sum > 0.0:
            return weights * (n / w_sum)

    if {"bid", "ask"}.issubset(chain_frame.columns):
        bid = chain_frame["bid"].to_numpy().astype(float)
        ask = chain_frame["ask"].to_numpy().astype(float)
        spread = np.maximum(ask - bid, 1e-4)
        w = 1.0 / spread
        w_sum = float(np.sum(w))
        if w_sum > 0:
            return w * (n / w_sum)
    return np.ones(n, dtype=float)


def _resolve_vol_col(chain_frame: pl.DataFrame, requested: str | None) -> str:
    candidates: list[str] = []
    if requested:
        candidates.append(requested)
    for name in ("implied_vol_input", "iv_ref", "implied_vol", "implied_vol_vendor"):
        if name not in candidates:
            candidates.append(name)
    for candidate in candidates:
        if candidate in chain_frame.columns:
            return candidate
    raise KeyError("no volatility column available for SSVI calibration")


def _coordinate(strikes: np.ndarray, forward: float, fit_space: FitSpace) -> np.ndarray:
    fwd = max(forward, 1e-12)
    if fit_space == "log":
        return np.log(np.maximum(strikes, 1e-12) / fwd)
    return (strikes - fwd) / fwd


def ssvi_forward(*, spot: float, rate: float, dividend: float, tau: float) -> float:
    return float(max(spot, 1e-12) * math.exp((rate - dividend) * tau))


def ssvi_total_variance_at(
    *,
    strike: float,
    forward: float,
    params: SSVIResult,
    fit_space: FitSpace = "log",
) -> float:
    coord = _coordinate(np.asarray([strike], dtype=float), forward=forward, fit_space=fit_space)
    w = _ssvi_total_variance(coord, np.asarray([params.a, params.b, params.rho, params.m, params.sigma], dtype=float))
    return float(max(float(w[0]), 1e-12))


def ssvi_implied_vol_at(
    *,
    strike: float,
    spot: float,
    tau: float,
    rate: float,
    dividend: float,
    params: SSVIResult,
    fit_space: FitSpace = "log",
) -> float:
    if not all(math.isfinite(x) for x in (strike, spot, tau, rate, dividend)) or strike <= 0.0 or spot <= 0.0 or tau <= 0.0:
        return float("nan")
    forward = ssvi_forward(spot=spot, rate=rate, dividend=dividend, tau=tau)
    total_variance = ssvi_total_variance_at(strike=strike, forward=forward, params=params, fit_space=fit_space)
    return float(math.sqrt(max(total_variance, 1e-12) / max(tau, 1e-12)))


def _ssvi_total_variance(coord: np.ndarray, p: np.ndarray) -> np.ndarray:
    a, b, rho, m, sigma = p
    return a + b * (rho * (coord - m) + np.sqrt((coord - m) ** 2 + sigma * sigma))


def _durrleman_penalty(params: np.ndarray, fit_space: FitSpace) -> float:
    _a, b, rho, _m, sigma = params
    if b <= 0.0 or sigma <= 0.0 or abs(rho) >= 1.0:
        return 1e3

    # Stronger arbitrage barrier in canonical log-moneyness space.
    if fit_space == "log":
        if b * (1.0 + abs(rho)) > 4.0:
            return (b * (1.0 + abs(rho)) - 4.0) * 1e3
    else:
        if b > 20.0:
            return (b - 20.0) * 1e2
    return 0.0


def calibrate_ssvi(
    chain_frame: pl.DataFrame,
    init_guess: dict[str, float] | None = None,
    constraints: SSVIConstraints | None = None,
    cold_start_multistart: bool = False,
    fit_space: FitSpace = "log",
    rate: float = 0.0,
    dividend: float = 0.0,
    vol_col: str = "implied_vol_input",
    weight_col: str | None = None,
) -> SSVIResult:
    constraints = constraints or SSVIConstraints()

    strikes = chain_frame["strike"].to_numpy().astype(float)
    resolved_vol_col = _resolve_vol_col(chain_frame, vol_col)
    vols = chain_frame[resolved_vol_col].to_numpy().astype(float)
    spot = float(chain_frame["underlying_price"][0])
    tau = _infer_tau(chain_frame)

    forward = spot * math.exp((rate - dividend) * tau)
    coord = _coordinate(strikes, forward=forward, fit_space=fit_space)
    target_w = np.maximum(vols, 1e-8) ** 2 * tau
    weights = _compute_weights(chain_frame, weight_col=weight_col)
    has_corridor = {"iv_bid", "iv_ask"}.issubset(chain_frame.columns)
    lower_vol = chain_frame["iv_bid"].to_numpy().astype(float) if "iv_bid" in chain_frame.columns else np.full_like(vols, np.nan)
    upper_vol = chain_frame["iv_ask"].to_numpy().astype(float) if "iv_ask" in chain_frame.columns else np.full_like(vols, np.nan)

    base = DEFAULT_INIT.copy()
    if init_guess:
        base = np.array(
            [
                init_guess.get("a", base[0]),
                init_guess.get("b", base[1]),
                init_guess.get("rho", base[2]),
                init_guess.get("m", base[3]),
                init_guess.get("sigma", base[4]),
            ],
            dtype=float,
        )

    lb = np.array([-5.0, constraints.b_min, constraints.rho_min, -5.0, constraints.sigma_min])
    ub = np.array([5.0, 10.0, constraints.rho_max, 5.0, 5.0])

    starts = [base]
    if cold_start_multistart:
        rng = np.random.default_rng(seed=42)
        for _ in range(5):
            starts.append(rng.uniform(lb, ub))

    if cold_start_multistart:
        max_nfev = 1000
    else:
        # Keep production log-space path tighter; allow strike-space comparison more room.
        max_nfev = 120 if fit_space == "log" else 260

    best: tuple[float, object] | None = None
    for guess in starts:

        def residuals(p: np.ndarray) -> np.ndarray:
            model_w = _ssvi_total_variance(coord, p)
            model_vol = np.sqrt(np.maximum(model_w, 1e-12) / max(tau, 1e-12))
            penalty = _durrleman_penalty(p, fit_space=fit_space)
            if has_corridor:
                corridor_scale = np.maximum(upper_vol - lower_vol, 1e-4)
                below = np.maximum(lower_vol - model_vol, 0.0) / corridor_scale
                above = np.maximum(model_vol - upper_vol, 0.0) / corridor_scale
                outside = 4.0 * (below + above)
                weak_guide = 0.05 * (model_vol - vols) / corridor_scale
                weighted = np.sqrt(weights) * (outside + weak_guide)
            else:
                weighted = np.sqrt(weights) * (model_w - target_w)
            return np.concatenate([weighted, np.array([penalty])])

        fit = least_squares(residuals, x0=guess, bounds=(lb, ub), max_nfev=max_nfev)
        objective = float(np.mean(residuals(fit.x) ** 2))

        if best is None or objective < best[0]:
            best = (objective, fit)

    assert best is not None
    objective, fit = best
    a, b, rho, m, sigma = fit.x
    penalty = _durrleman_penalty(fit.x, fit_space=fit_space)
    if fit_space == "log":
        durrleman_pass = penalty <= 1e-8
        success = bool(fit.success) or (objective < 1e-10 and durrleman_pass)
    else:
        durrleman_pass = penalty <= 1e-5
        success = bool(fit.success) or (objective < 1e-6 and durrleman_pass)
    return SSVIResult(
        a=float(a),
        b=float(b),
        rho=float(rho),
        m=float(m),
        sigma=float(sigma),
        objective=objective,
        success=success,
        iterations=int(fit.nfev),
        durrleman_pass=durrleman_pass,
    )


def calibrate_ssvi_cpp(
    chain_frame: pl.DataFrame,
    init_guess: dict[str, float] | None = None,
    constraints: SSVIConstraints | None = None,
    fit_space: FitSpace = "log",
    rate: float = 0.0,
    dividend: float = 0.0,
    vol_col: str = "implied_vol_input",
    weight_col: str | None = None,
) -> tuple[SSVIResult, dict[str, object]]:
    if quantcore is None:
        raise RuntimeError("quantcore module not available")
    if chain_frame.is_empty():
        raise RuntimeError("empty_chain")

    constraints = constraints or SSVIConstraints()
    strikes = chain_frame["strike"].to_numpy().astype(float)
    resolved_vol_col = _resolve_vol_col(chain_frame, vol_col)
    vols = chain_frame[resolved_vol_col].to_numpy().astype(float)
    spot = float(chain_frame["underlying_price"][0])
    tau = _infer_tau(chain_frame)
    forward = spot * math.exp((rate - dividend) * tau)
    weights = _compute_weights(chain_frame, weight_col=weight_col)
    lower_vol = chain_frame["iv_bid"].to_numpy().astype(float) if "iv_bid" in chain_frame.columns else np.array([], dtype=float)
    upper_vol = chain_frame["iv_ask"].to_numpy().astype(float) if "iv_ask" in chain_frame.columns else np.array([], dtype=float)

    if not hasattr(quantcore, "ssvi_residuals_slice"):
        raise RuntimeError("quantcore ssvi_residuals_slice entrypoint not available")

    base = DEFAULT_INIT.copy()
    if init_guess:
        base = np.array(
            [
                init_guess.get("a", base[0]),
                init_guess.get("b", base[1]),
                init_guess.get("rho", base[2]),
                init_guess.get("m", base[3]),
                init_guess.get("sigma", base[4]),
            ],
            dtype=float,
        )

    lb = np.array([-5.0, constraints.b_min, constraints.rho_min, -5.0, constraints.sigma_min])
    ub = np.array([5.0, 10.0, constraints.rho_max, 5.0, 5.0])
    max_nfev = 120 if fit_space == "log" else 260

    def residuals(params: np.ndarray) -> np.ndarray:
        payload = quantcore.ssvi_residuals_slice(
            strikes.tolist(),
            vols.tolist(),
            weights.tolist(),
            lower_vol.tolist(),
            upper_vol.tolist(),
            float(forward),
            float(tau),
            fit_space,
            params.tolist(),
        )
        return np.asarray(payload, dtype=float)

    fit = least_squares(residuals, x0=base, bounds=(lb, ub), max_nfev=max_nfev)
    objective = float(np.mean(residuals(fit.x) ** 2))
    params = fit.x.tolist()
    converged = bool(fit.success)
    durrleman = _durrleman_penalty(fit.x, fit_space=fit_space) <= (1e-8 if fit_space == "log" else 1e-5)
    iterations = int(fit.nfev)
    sse = float(np.sum(residuals(fit.x) ** 2))
    if fit_space == "log":
        success = bool(converged) or (objective < 1e-10 and durrleman)
    else:
        success = bool(converged) or (objective < 1e-6 and durrleman)
    reason = "converged" if converged and durrleman else ("durrleman_violation" if not durrleman else "max_nfev")
    result = SSVIResult(
        a=float(params[0]),
        b=float(params[1]),
        rho=float(params[2]),
        m=float(params[3]),
        sigma=float(params[4]),
        objective=objective,
        success=bool(success and durrleman),
        iterations=iterations,
        durrleman_pass=durrleman,
    )
    meta: dict[str, object] = {
        "backend_used": "cpp",
        "converged": converged,
        "durrleman": durrleman,
        "objective": objective,
        "sse": sse,
        "iterations": iterations,
        "reason": reason,
        "fit_space": fit_space,
        "has_corridor": bool(lower_vol.size and upper_vol.size),
    }
    return result, meta

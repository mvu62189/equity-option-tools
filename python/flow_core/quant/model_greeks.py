from __future__ import annotations

import math

import polars as pl

from .dividends import DividendEvent, escrowed_spot
from .fdm_cn import price_greeks_crank_nicolson
from .laplace_zhu import price_laplace_zhu_call, price_laplace_zhu_put
from .luba_rim import calc_rim_call, calc_rim_put
from .models import AmericanContract, SSVIResult
from .routed_greeks import (
    ROUTED_GREEKS_COLUMNS,
    _nan_result,
    _projected_divs_for_row,
    _row_base,
    _sum_dividends,
    _tau_years,
    _to_cpp_divs,
)
from .ssvi import FitSpace, ssvi_implied_vol_at
from .tree_richardson import price_tree_richardson

try:  # optional fast path
    import quantcore  # type: ignore
except Exception:  # pragma: no cover
    quantcore = None


def _surface_params(row: dict) -> SSVIResult | None:
    needed = ("ssvi_a", "ssvi_b", "ssvi_rho", "ssvi_m", "ssvi_sigma")
    if not all(k in row and math.isfinite(float(row[k])) for k in needed):
        return None
    return SSVIResult(
        a=float(row["ssvi_a"]),
        b=float(row["ssvi_b"]),
        rho=float(row["ssvi_rho"]),
        m=float(row["ssvi_m"]),
        sigma=float(row["ssvi_sigma"]),
        objective=float(row.get("ssvi_objective", float("nan")) or float("nan")),
        success=bool(row.get("ssvi_success", False)),
        iterations=int(row.get("ssvi_iterations", 0) or 0),
        durrleman_pass=bool(row.get("ssvi_durrleman_pass", True)),
    )


def _sticky_surface_vol(
    *,
    row: dict,
    params: SSVIResult,
    spot: float,
    tau: float,
    rate: float,
    fit_space: FitSpace,
) -> float:
    return float(
        ssvi_implied_vol_at(
            strike=float(row.get("strike", float("nan"))),
            spot=spot,
            tau=tau,
            rate=rate,
            dividend=0.0,
            params=params,
            fit_space=fit_space,
        )
    )


def _finite_diff_sticky_surface(
    *,
    row: dict,
    contract: AmericanContract,
    params: SSVIResult,
    fit_space: FitSpace,
    price_fn,
) -> tuple[float, float, float, float, float, float, float]:
    ds = max(contract.spot * 1e-4, 1e-4)
    dr = 1e-4
    dt = 1.0 / 365.0
    dvol = 1e-4

    base_vol = _sticky_surface_vol(
        row=row,
        params=params,
        spot=contract.spot,
        tau=contract.tau,
        rate=contract.rate,
        fit_space=fit_space,
    )
    if not math.isfinite(base_vol) or base_vol <= 0.0:
        return float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), float("nan")

    base = price_fn(contract.spot, contract.tau, contract.rate, base_vol)
    up_s_vol = _sticky_surface_vol(row=row, params=params, spot=contract.spot + ds, tau=contract.tau, rate=contract.rate, fit_space=fit_space)
    dn_s_spot = max(contract.spot - ds, 1e-6)
    dn_s_vol = _sticky_surface_vol(row=row, params=params, spot=dn_s_spot, tau=contract.tau, rate=contract.rate, fit_space=fit_space)
    up_s = price_fn(contract.spot + ds, contract.tau, contract.rate, up_s_vol)
    dn_s = price_fn(dn_s_spot, contract.tau, contract.rate, dn_s_vol)
    delta = (up_s - dn_s) / (2.0 * ds)
    gamma = (up_s - 2.0 * base + dn_s) / (ds * ds)

    prev_tau = max(contract.tau - dt, 1e-8)
    prev_tau_vol = _sticky_surface_vol(row=row, params=params, spot=contract.spot, tau=prev_tau, rate=contract.rate, fit_space=fit_space)
    prev_t = price_fn(contract.spot, prev_tau, contract.rate, prev_tau_vol)
    theta = (prev_t - base) / dt

    up_r_vol = _sticky_surface_vol(row=row, params=params, spot=contract.spot, tau=contract.tau, rate=contract.rate + dr, fit_space=fit_space)
    dn_r_vol = _sticky_surface_vol(row=row, params=params, spot=contract.spot, tau=contract.tau, rate=contract.rate - dr, fit_space=fit_space)
    up_r = price_fn(contract.spot, contract.tau, contract.rate + dr, up_r_vol)
    dn_r = price_fn(contract.spot, contract.tau, contract.rate - dr, dn_r_vol)
    rho = (up_r - dn_r) / (2.0 * dr)

    up_vol = price_fn(contract.spot, contract.tau, contract.rate, base_vol + dvol)
    dn_vol = price_fn(contract.spot, contract.tau, contract.rate, max(base_vol - dvol, 1e-4))
    vega = (up_vol - dn_vol) / (2.0 * dvol)
    return base_vol, float(base), float(delta), float(gamma), float(theta), float(vega), float(rho)


def compute_model_greeks(
    frame: pl.DataFrame,
    *,
    dividend_source,
    tree_steps: int = 100,
    rim_nodes: int = 100,
    fdm_scheme: str = "log",
    fdm_backend: str = "auto",
    laplace_m: int = 12,
    runtime_mode: str = "live_strict",
    fit_space: FitSpace = "log",
) -> pl.DataFrame:
    needed = {
        "symbol",
        "asof_ts",
        "expiration",
        "option_type",
        "strike",
        "underlying_price",
        "greeks_engine",
        "ssvi_a",
        "ssvi_b",
        "ssvi_rho",
        "ssvi_m",
        "ssvi_sigma",
    }
    if frame.is_empty() or not needed.issubset(frame.columns):
        return pl.DataFrame(schema={**{col: pl.Float64 for col in ROUTED_GREEKS_COLUMNS if col not in {"symbol", "contract_symbol", "input_snapshot_kind", "option_type", "greeks_engine", "engine_used", "backend_used", "fallback_reason", "runtime_mode", "jump_interp_mode", "space_mode", "theta_convention", "vega_method", "rho_method", "display_price_source", "error"}}, "symbol": pl.String})

    out_rows: list[dict] = []
    for row in frame.to_dicts():
        row["runtime_mode"] = runtime_mode
        row["backend_used"] = "python"
        row["fallback_reason"] = ""
        row["jump_interp_mode"] = ""
        row["space_mode"] = fit_space
        params = _surface_params(row)
        if params is None or not params.success:
            out_rows.append(_nan_result(row, "model_surface", "missing_ssvi_surface"))
            continue

        spot = float(row.get("underlying_price") or float("nan"))
        strike = float(row.get("strike") or float("nan"))
        if not (math.isfinite(spot) and spot > 0.0 and math.isfinite(strike) and strike > 0.0):
            out_rows.append(_nan_result(row, "model_surface", "invalid_contract"))
            continue

        tau = _tau_years(row.get("expiration"), row.get("asof_ts"))
        rate = float(row.get("rate_used", 0.0) or 0.0)
        projected_divs = _projected_divs_for_row(row, tau=tau, dividend_source=dividend_source)
        div_policy = str(row.get("dividend_policy", "")).lower()
        use_node_event = div_policy == "node_event_exact"
        use_escrow = div_policy == "escrowed"
        if not div_policy:
            use_node_event = int(row.get("days_to_expiry", 0)) < 31
            use_escrow = not use_node_event

        contract = AmericanContract(
            spot=spot,
            strike=strike,
            rate=rate,
            dividend=0.0,
            tau=tau,
            is_call=str(row.get("option_type", "")).lower() == "call",
        )
        row["tau_years"] = tau
        row["rate_used"] = rate
        row["dividend_used"] = _sum_dividends(projected_divs)
        engine = str(row.get("greeks_engine", "")).lower()
        is_call = contract.is_call

        def price_fn(s: float, tau_: float, r: float, sigma: float) -> float:
            shifted_divs = projected_divs if use_node_event else []
            if engine == "crank_nicolson_fdm":
                if fdm_backend in {"cpp", "auto"} and quantcore is not None and hasattr(quantcore, "fdm_cn_log_greeks"):
                    payload = quantcore.fdm_cn_log_greeks(
                        float(s),
                        float(contract.strike),
                        float(tau_),
                        float(r),
                        0.0,
                        float(sigma),
                        bool(is_call),
                        200,
                        220,
                        _to_cpp_divs(shifted_divs),
                    )
                    return float(payload.get("price", float("nan")))
                bumped = AmericanContract(spot=s, strike=contract.strike, rate=r, dividend=0.0, tau=tau_, is_call=is_call)
                return float(price_greeks_crank_nicolson(bumped, vol=sigma, scheme=fdm_scheme, divs=shifted_divs).price)  # type: ignore[arg-type]
            if engine == "binomial_richardson":
                bumped = AmericanContract(spot=s, strike=contract.strike, rate=r, dividend=0.0, tau=tau_, is_call=is_call)
                return float(price_tree_richardson(bumped, sigma, steps=tree_steps, divs=shifted_divs))
            if engine == "rim":
                spot_eff = escrowed_spot(s, rate=r, tau=tau_, divs=projected_divs) if use_escrow else s
                if is_call:
                    return float(calc_rim_call(spot_eff, contract.strike, tau_, r, 0.0, sigma, n=rim_nodes))
                return float(calc_rim_put(spot_eff, contract.strike, tau_, r, 0.0, sigma, n=rim_nodes))
            if engine == "laplace_transform_zhu":
                spot_eff = escrowed_spot(s, rate=r, tau=tau_, divs=projected_divs) if use_escrow else s
                if is_call:
                    return float(price_laplace_zhu_call(spot_eff, contract.strike, tau_, r, 0.0, sigma, m=laplace_m))
                return float(price_laplace_zhu_put(spot_eff, contract.strike, tau_, r, 0.0, sigma, m=laplace_m))
            if engine == "bjerksund_stensland" and quantcore is not None:
                cpp_divs = _to_cpp_divs(projected_divs if use_escrow else [])
                if is_call and hasattr(quantcore, "bs2002_greeks_call"):
                    price, *_ = quantcore.bs2002_greeks_call(float(s), float(contract.strike), float(tau_), float(r), float(sigma), cpp_divs)
                    return float(price)
                if (not is_call) and hasattr(quantcore, "bs2002_greeks_put"):
                    price, *_ = quantcore.bs2002_greeks_put(float(s), float(contract.strike), float(tau_), float(r), float(sigma), cpp_divs)
                    return float(price)
            return float("nan")

        try:
            model_vol, price, delta, gamma, theta, vega, rho = _finite_diff_sticky_surface(
                row=row,
                contract=contract,
                params=params,
                fit_space=fit_space,
                price_fn=price_fn,
            )
        except Exception as exc:
            out_rows.append(_nan_result(row, "model_surface", f"sticky_surface_exception:{exc}"))
            continue
        if not all(math.isfinite(x) for x in (model_vol, price, delta, gamma, theta, vega, rho)):
            out_rows.append(_nan_result(row, "model_surface", "sticky_surface_invalid"))
            continue

        row["implied_vol_input"] = float(model_vol)
        base = _row_base(row)
        base.update(
            {
                "engine_used": f"{engine or 'unknown'}_sticky_surface",
                "backend_used": "python",
                "price": float(price),
                "model_price": float(price),
                "display_price": float(price),
                "display_price_source": "model_price",
                "delta": float(delta),
                "gamma": float(gamma),
                "theta": float(theta),
                "vega": float(vega),
                "rho": float(rho),
                "vega_method": "finite_difference_local_vol_sticky_surface",
                "rho_method": "finite_difference_sticky_surface",
                "success": True,
                "error": "",
                "greeks_source": "model_greeks",
                "sticky_mode": "delta_moneyness",
                "ssvi_fit_space": fit_space,
            }
        )
        out_rows.append(base)

    if not out_rows:
        return pl.DataFrame(schema={})
    return pl.DataFrame(out_rows)

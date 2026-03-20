from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Callable

import polars as pl

from .fdm_cn import price_greeks_crank_nicolson
from .laplace_zhu import (
    price_laplace_zhu_call,
    price_laplace_zhu_put,
    price_vega_rho_laplace_zhu_call,
    price_vega_rho_laplace_zhu_put,
)
from .dividends import DividendEvent, escrowed_spot
from .luba_rim import calc_rim_call, calc_rim_put
from .market_inputs import HybridDividendSource, TBillRateCurve
from .models import AmericanContract
from .tree_richardson import greeks_tree_richardson, price_tree_richardson

try:  # optional fast path
    import quantcore  # type: ignore
except Exception:  # pragma: no cover
    quantcore = None


ROUTED_GREEKS_COLUMNS = [
    "symbol",
    "asof_ts",
    "batch_id",
    "input_snapshot_kind",
    "expiration",
    "option_type",
    "strike",
    "underlying_price",
    "implied_vol",
    "days_to_expiry",
    "tau_years",
    "market_bid",
    "market_ask",
    "market_last",
    "market_mid",
    "greeks_engine",
    "engine_used",
    "backend_used",
    "fallback_reason",
    "runtime_mode",
    "jump_interp_mode",
    "space_mode",
    "rate_used",
    "dividend_used",
    "theta_convention",
    "vega_method",
    "rho_method",
    "price",
    "model_price",
    "display_price",
    "display_price_source",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
    "success",
    "error",
]


def _empty_greeks() -> pl.DataFrame:
    return pl.DataFrame({c: [] for c in ROUTED_GREEKS_COLUMNS})


def _to_utc_datetime(value: datetime | date | None) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, 16, 0, 0, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _tau_years(expiration: date | datetime | None, asof: datetime | date | None) -> float:
    exp_dt = _to_utc_datetime(expiration)
    asof_dt = _to_utc_datetime(asof)
    sec = max((exp_dt - asof_dt).total_seconds(), 60.0)
    return max(sec / (365.25 * 24.0 * 3600.0), 1.0 / 365.0)


def _market_mid(row: dict) -> float:
    bid = row.get("bid")
    ask = row.get("ask")
    try:
        bid_f = float(bid)
        ask_f = float(ask)
        if math.isfinite(bid_f) and math.isfinite(ask_f) and (bid_f > 0.0 or ask_f > 0.0):
            return 0.5 * (bid_f + ask_f)
    except Exception:
        pass
    try:
        last = float(row.get("last", float("nan")))
        return last
    except Exception:
        return float("nan")


def _sum_dividends(divs: list[DividendEvent]) -> float:
    return float(sum(float(d.amount) for d in divs if float(d.amount) > 0.0))


def _shift_divs(divs: list[DividendEvent], shift_years: float) -> list[DividendEvent]:
    if shift_years <= 0.0 or not divs:
        return list(divs)
    out: list[DividendEvent] = []
    for div in divs:
        t = float(div.time_to_ex_date) - shift_years
        if t > 0.0 and float(div.amount) > 0.0:
            out.append(DividendEvent(amount=float(div.amount), time_to_ex_date=t))
    return out


def _projected_divs_for_row(
    row: dict,
    tau: float,
    dividend_source: HybridDividendSource | None,
) -> list[DividendEvent]:
    if dividend_source is None:
        return []
    symbol = str(row.get("symbol", "")).upper()
    if not symbol:
        return []
    asof = _to_utc_datetime(row.get("asof_ts"))
    try:
        return dividend_source.projected_dividends(symbol=symbol, asof_ts=asof, tau_years=tau)
    except Exception:
        return []


def _finite_diff_greeks(
    price_fn: Callable[[float, float, float, float], float],
    *,
    spot: float,
    tau: float,
    vol: float,
    rate: float,
) -> tuple[float, float, float, float, float, float]:
    base = price_fn(spot, tau, vol, rate)
    ds = max(spot * 1e-4, 1e-4)
    dvol = 1e-4
    dr = 1e-4
    dt = 1.0 / 365.0

    up_s = price_fn(spot + ds, tau, vol, rate)
    dn_s = price_fn(max(spot - ds, 1e-6), tau, vol, rate)
    delta = (up_s - dn_s) / (2.0 * ds)
    gamma = (up_s - 2.0 * base + dn_s) / (ds * ds)

    up_vol = price_fn(spot, tau, vol + dvol, rate)
    dn_vol = price_fn(spot, tau, max(vol - dvol, 1e-4), rate)
    vega = (up_vol - dn_vol) / (2.0 * dvol)

    up_r = price_fn(spot, tau, vol, rate + dr)
    dn_r = price_fn(spot, tau, vol, rate - dr)
    rho = (up_r - dn_r) / (2.0 * dr)

    prev_t = price_fn(spot, max(tau - dt, 1e-8), vol, rate)
    theta = (prev_t - base) / dt
    return float(base), float(delta), float(gamma), float(theta), float(vega), float(rho)


def _row_base(row: dict) -> dict:
    tau = float(row.get("tau_years", float("nan"))) if row.get("tau_years") is not None else float("nan")
    return {
        "symbol": str(row.get("symbol", "")),
        "asof_ts": row.get("asof_ts"),
        "batch_id": str(row.get("batch_id", "")),
        "input_snapshot_kind": str(row.get("snapshot_kind", "")),
        "expiration": row.get("expiration"),
        "option_type": str(row.get("option_type", "")).lower(),
        "strike": float(row.get("strike", float("nan"))),
        "underlying_price": float(row.get("underlying_price", float("nan"))),
        "implied_vol": float(row.get("implied_vol_vendor", float("nan"))),
        "days_to_expiry": int(row.get("days_to_expiry", 0)),
        "tau_years": tau,
        "market_bid": float(row.get("bid", float("nan"))),
        "market_ask": float(row.get("ask", float("nan"))),
        "market_last": float(row.get("last", float("nan"))),
        "market_mid": _market_mid(row),
        "greeks_engine": str(row.get("greeks_engine", "")),
        "backend_used": str(row.get("backend_used", "python")),
        "fallback_reason": str(row.get("fallback_reason", "")),
        "runtime_mode": str(row.get("runtime_mode", "")),
        "jump_interp_mode": str(row.get("jump_interp_mode", "")),
        "space_mode": str(row.get("space_mode", "strike")),
        "rate_used": float(row.get("rate_used", float("nan"))),
        "dividend_used": float(row.get("dividend_used", float("nan"))),
        "theta_convention": "1-calendar-day forward difference, per day",
        "vega_method": str(row.get("vega_method", "")),
        "rho_method": str(row.get("rho_method", "")),
    }


def _nan_result(row: dict, engine_used: str, error: str) -> dict:
    base = _row_base(row)
    base.update(
        {
            "engine_used": engine_used,
            "price": float("nan"),
            "model_price": float("nan"),
            "display_price": float("nan"),
            "display_price_source": "model_price",
            "delta": float("nan"),
            "gamma": float("nan"),
            "theta": float("nan"),
            "vega": float("nan"),
            "rho": float("nan"),
            "success": False,
            "error": error,
        }
    )
    return base


def _tree_result(
    row: dict,
    contract: AmericanContract,
    vol: float,
    *,
    steps: int,
    divs: list[DividendEvent],
    engine_used: str = "binomial_richardson",
) -> dict:
    out = greeks_tree_richardson(contract, vol=vol, steps=steps, divs=divs)
    shift_base = float(contract.tau)

    def tree_price_fn(s: float, tau: float, sigma: float, r: float) -> float:
        shifted_divs = _shift_divs(divs, max(shift_base - tau, 0.0))
        bumped = AmericanContract(
            spot=s,
            strike=contract.strike,
            rate=r,
            dividend=contract.dividend,
            tau=tau,
            is_call=contract.is_call,
        )
        return float(price_tree_richardson(bumped, sigma, steps=steps, divs=shifted_divs))

    _, _, _, _, vega, rho = _finite_diff_greeks(
        tree_price_fn,
        spot=contract.spot,
        tau=contract.tau,
        vol=vol,
        rate=contract.rate,
    )
    base = _row_base(row)
    base.update(
        {
            "engine_used": engine_used,
            "backend_used": "python",
            "space_mode": "strike",
            "price": float(out.price),
            "model_price": float(out.price),
            "display_price": float(out.price),
            "display_price_source": "model_price",
            "delta": float(out.delta),
            "gamma": float(out.gamma),
            "theta": float(out.theta),
            "vega": float(vega),
            "rho": float(rho),
            "vega_method": "finite_difference_tree",
            "rho_method": "finite_difference_tree",
            "success": bool(out.success and all(math.isfinite(x) for x in (out.price, out.delta, out.gamma, out.theta, vega, rho))),
            "error": "",
        }
    )
    return base


def _fdm_result(
    row: dict,
    contract: AmericanContract,
    vol: float,
    *,
    scheme: str,
    divs: list[DividendEvent],
    backend: str,
    runtime_mode: str,
) -> dict:
    want_cpp = backend in {"cpp", "auto"}
    fallback_reason = ""
    shift_base = float(contract.tau)

    def fdm_price_fn(s: float, tau: float, sigma: float, r: float) -> float:
        shifted_divs = _shift_divs(divs, max(shift_base - tau, 0.0))
        bumped = AmericanContract(
            spot=s,
            strike=contract.strike,
            rate=r,
            dividend=contract.dividend,
            tau=tau,
            is_call=contract.is_call,
        )
        if want_cpp and quantcore is not None and hasattr(quantcore, "fdm_cn_log_greeks"):
            payload = quantcore.fdm_cn_log_greeks(
                float(bumped.spot),
                float(bumped.strike),
                float(bumped.tau),
                float(bumped.rate),
                float(bumped.dividend),
                float(sigma),
                bool(bumped.is_call),
                200,
                220,
                _to_cpp_divs(shifted_divs),
            )
            return float(payload.get("price", float("nan")))
        return float(price_greeks_crank_nicolson(bumped, vol=sigma, scheme=scheme, divs=shifted_divs).price)  # type: ignore[arg-type]

    if want_cpp:
        if quantcore is None or not hasattr(quantcore, "fdm_cn_log_greeks"):
            fallback_reason = "fdm_cpp_unavailable"
            if runtime_mode == "live_strict":
                return _nan_result(row, "fdm_cn_log_cpp", f"{fallback_reason}_strict")
        else:
            try:
                payload = quantcore.fdm_cn_log_greeks(
                    float(contract.spot),
                    float(contract.strike),
                    float(contract.tau),
                    float(contract.rate),
                    float(contract.dividend),
                    float(vol),
                    bool(contract.is_call),
                    200,
                    220,
                    _to_cpp_divs(divs),
                )
                if bool(payload.get("success", False)):
                    _, _, _, _, vega_cpp_fd, rho_cpp_fd = _finite_diff_greeks(
                        fdm_price_fn,
                        spot=contract.spot,
                        tau=contract.tau,
                        vol=vol,
                        rate=contract.rate,
                    )
                    base = _row_base(row)
                    base.update(
                        {
                            "engine_used": "fdm_cn_log_cpp",
                            "backend_used": "cpp",
                            "fallback_reason": "",
                            "jump_interp_mode": str(payload.get("jump_interp_mode", "")),
                            "space_mode": "log",
                            "price": float(payload.get("price", float("nan"))),
                            "model_price": float(payload.get("price", float("nan"))),
                            "display_price": float(payload.get("price", float("nan"))),
                            "display_price_source": "model_price",
                            "delta": float(payload.get("delta", float("nan"))),
                            "gamma": float(payload.get("gamma", float("nan"))),
                            "theta": float(payload.get("theta", float("nan"))),
                            "vega": float(vega_cpp_fd),
                            "rho": float(rho_cpp_fd),
                            "vega_method": "finite_difference_fdm_cpp",
                            "rho_method": "finite_difference_fdm_cpp",
                            "success": bool(
                                all(
                                    math.isfinite(float(x))
                                    for x in (
                                        payload.get("price", float("nan")),
                                        payload.get("delta", float("nan")),
                                        payload.get("gamma", float("nan")),
                                        payload.get("theta", float("nan")),
                                        vega_cpp_fd,
                                        rho_cpp_fd,
                                    )
                                )
                            ),
                            "error": "",
                        }
                    )
                    return base
                fallback_reason = str(payload.get("reason", "fdm_cpp_failed"))
                if runtime_mode == "live_strict":
                    return _nan_result(row, "fdm_cn_log_cpp", f"{fallback_reason}_strict")
            except Exception as exc:
                fallback_reason = f"fdm_cpp_exception:{exc}"
                if runtime_mode == "live_strict":
                    return _nan_result(row, "fdm_cn_log_cpp", fallback_reason)

    out = price_greeks_crank_nicolson(contract, vol=vol, scheme=scheme, divs=divs)  # type: ignore[arg-type]
    _, _, _, _, vega, rho = _finite_diff_greeks(
        fdm_price_fn,
        spot=contract.spot,
        tau=contract.tau,
        vol=vol,
        rate=contract.rate,
    )
    base = _row_base(row)
    base.update(
        {
            "engine_used": f"crank_nicolson_{scheme}",
            "backend_used": "python",
            "fallback_reason": fallback_reason,
            "jump_interp_mode": "n/a",
            "space_mode": scheme,
            "price": float(out.price),
            "model_price": float(out.price),
            "display_price": float(out.price),
            "display_price_source": "model_price",
            "delta": float(out.delta),
            "gamma": float(out.gamma),
            "theta": float(out.theta),
            "vega": float(vega),
            "rho": float(rho),
            "vega_method": "finite_difference_fdm",
            "rho_method": "finite_difference_fdm",
            "success": bool(out.success and all(math.isfinite(x) for x in (out.price, out.delta, out.gamma, out.theta, vega, rho))),
            "error": "",
        }
    )
    return base


def _to_cpp_divs(divs: list[DividendEvent]) -> list[tuple[float, float]]:
    return [(float(d.amount), float(d.time_to_ex_date)) for d in divs if d.amount > 0.0 and d.time_to_ex_date > 0.0]


def _bs2002_result(row: dict, contract: AmericanContract, vol: float, *, divs: list[DividendEvent]) -> dict:
    if quantcore is None:
        return _nan_result(row, "bs2002_cpp", "bs2002_cpp_unavailable")
    is_call = str(row.get("option_type", "")).lower() == "call"
    try:
        cpp_divs = _to_cpp_divs(divs)
        if is_call:
            p, d, g, t, v, r = quantcore.bs2002_greeks_call(
                float(contract.spot),
                float(contract.strike),
                float(contract.tau),
                float(contract.rate),
                float(vol),
                cpp_divs,
            )
        else:
            p, d, g, t, v, r = quantcore.bs2002_greeks_put(
                float(contract.spot),
                float(contract.strike),
                float(contract.tau),
                float(contract.rate),
                float(vol),
                cpp_divs,
            )
        base = _row_base(row)
        vals = [float(p), float(d), float(g), float(t), float(v), float(r)]
        base.update(
            {
                "engine_used": "bs2002_cpp",
                "backend_used": "cpp",
                "space_mode": "strike",
                "price": vals[0],
                "model_price": vals[0],
                "display_price": vals[0],
                "display_price_source": "model_price",
                "delta": vals[1],
                "gamma": vals[2],
                "theta": vals[3],
                "vega": vals[4],
                "rho": vals[5],
                "vega_method": "cfd_bs2002_cpp",
                "rho_method": "cfd_bs2002_cpp",
                "success": all(math.isfinite(x) for x in vals),
                "error": "",
            }
        )
        return base
    except Exception as exc:
        return _nan_result(row, "bs2002_cpp", str(exc))


def _rim_result(
    row: dict,
    contract: AmericanContract,
    vol: float,
    *,
    rim_nodes: int,
    use_escrow: bool,
    divs: list[DividendEvent],
) -> dict:
    is_call = str(row.get("option_type", "")).lower() == "call"
    spot_eff = escrowed_spot(contract.spot, rate=contract.rate, tau=contract.tau, divs=divs) if use_escrow else contract.spot

    def rim_price(s: float, tau: float, sigma: float, r: float) -> float:
        if is_call:
            return float(calc_rim_call(s, contract.strike, tau, r, 0.0, sigma, n=rim_nodes))
        return float(calc_rim_put(s, contract.strike, tau, r, 0.0, sigma, n=rim_nodes))

    price, delta, gamma, theta, vega, rho = _finite_diff_greeks(
        rim_price,
        spot=spot_eff,
        tau=contract.tau,
        vol=vol,
        rate=contract.rate,
    )
    base = _row_base(row)
    vals = [price, delta, gamma, theta, vega, rho]
    base.update(
        {
            "engine_used": "rim_fd",
            "backend_used": "python",
            "space_mode": "strike",
            "price": price,
            "model_price": price,
            "display_price": price,
            "display_price_source": "model_price",
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
            "rho": rho,
            "vega_method": "finite_difference_rim",
            "rho_method": "finite_difference_rim",
            "success": all(math.isfinite(x) for x in vals),
            "error": "",
        }
    )
    return base


def _laplace_result(
    row: dict,
    contract: AmericanContract,
    vol: float,
    *,
    laplace_m: int,
    use_escrow: bool,
    divs: list[DividendEvent],
) -> dict:
    is_call = str(row.get("option_type", "")).lower() == "call"
    spot_eff = escrowed_spot(contract.spot, rate=contract.rate, tau=contract.tau, divs=divs) if use_escrow else contract.spot
    q_eff = 0.0 if use_escrow else contract.dividend

    use_cpp = (
        quantcore is not None
        and hasattr(quantcore, "laplace_zhu_call")
        and hasattr(quantcore, "laplace_zhu_put")
        and hasattr(quantcore, "laplace_zhu_call_vega_rho")
        and hasattr(quantcore, "laplace_zhu_put_vega_rho")
    )
    if use_cpp:
        try:
            if is_call:
                price_cpp, vega_cpp, rho_cpp = quantcore.laplace_zhu_call_vega_rho(
                    float(spot_eff),
                    float(contract.strike),
                    float(contract.tau),
                    float(contract.rate),
                    float(q_eff),
                    float(vol),
                    int(laplace_m),
                )
            else:
                price_cpp, vega_cpp, rho_cpp = quantcore.laplace_zhu_put_vega_rho(
                    float(spot_eff),
                    float(contract.strike),
                    float(contract.tau),
                    float(contract.rate),
                    float(q_eff),
                    float(vol),
                    int(laplace_m),
                )
            lap_success = all(math.isfinite(float(x)) for x in (price_cpp, vega_cpp, rho_cpp))
            lap_vega = float(vega_cpp)
            lap_rho = float(rho_cpp)
            lap_engine = "laplace_zhu_cpp"
        except Exception:
            use_cpp = False
            lap_success = False
            lap_vega = float("nan")
            lap_rho = float("nan")
            lap_engine = "laplace_zhu"
    if not use_cpp:
        if is_call:
            lap = price_vega_rho_laplace_zhu_call(
                spot_eff,
                contract.strike,
                contract.tau,
                contract.rate,
                q_eff,
                vol,
                m=laplace_m,
            )
        else:
            lap = price_vega_rho_laplace_zhu_put(
                spot_eff,
                contract.strike,
                contract.tau,
                contract.rate,
                q_eff,
                vol,
                m=laplace_m,
            )
        lap_success = bool(lap.success)
        lap_vega = float(lap.vega)
        lap_rho = float(lap.rho)
        lap_engine = "laplace_zhu"

    def laplace_price(s: float, tau: float, sigma: float, r: float) -> float:
        if use_cpp:
            if is_call:
                return float(quantcore.laplace_zhu_call(s, contract.strike, tau, r, q_eff, sigma, int(laplace_m)))
            return float(quantcore.laplace_zhu_put(s, contract.strike, tau, r, q_eff, sigma, int(laplace_m)))
        if is_call:
            return float(price_laplace_zhu_call(s, contract.strike, tau, r, q_eff, sigma, m=laplace_m))
        return float(price_laplace_zhu_put(s, contract.strike, tau, r, q_eff, sigma, m=laplace_m))

    base_price, delta, gamma, theta, _vega_fd, _rho_fd = _finite_diff_greeks(
        laplace_price,
        spot=spot_eff,
        tau=contract.tau,
        vol=vol,
        rate=contract.rate,
    )

    base = _row_base(row)
    vals = [base_price, delta, gamma, theta, lap_vega, lap_rho]
    base.update(
        {
            "engine_used": lap_engine,
            "backend_used": "cpp" if lap_engine.endswith("_cpp") else "python",
            "space_mode": "strike",
            "price": base_price,
            "model_price": base_price,
            "display_price": base_price,
            "display_price_source": "model_price",
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": lap_vega,
            "rho": lap_rho,
            "vega_method": "native_laplace" if use_cpp else "native_laplace_python",
            "rho_method": "native_laplace" if use_cpp else "native_laplace_python",
            "success": bool(lap_success and all(math.isfinite(x) for x in vals)),
            "error": "",
        }
    )
    return base


def compute_routed_greeks(
    frame: pl.DataFrame,
    *,
    rate: float = 0.04,
    dividend: float = 0.0,
    rate_curve: TBillRateCurve | None = None,
    dividend_source: HybridDividendSource | None = None,
    tree_steps: int = 100,
    rim_nodes: int = 100,
    fdm_scheme: str = "log",
    laplace_m: int = 12,
    fdm_backend: str = "auto",
    runtime_mode: str = "live_strict",
) -> pl.DataFrame:
    needed = {
        "symbol",
        "asof_ts",
        "expiration",
        "option_type",
        "strike",
        "underlying_price",
        "implied_vol_vendor",
        "greeks_engine",
    }
    if frame.is_empty() or not needed.issubset(frame.columns):
        return _empty_greeks()

    out_rows: list[dict] = []
    for row in frame.to_dicts():
        row["runtime_mode"] = runtime_mode
        row["backend_used"] = "python"
        row["fallback_reason"] = ""
        row["jump_interp_mode"] = ""
        row["space_mode"] = "strike"
        vol = float(row.get("implied_vol_vendor") or float("nan"))
        spot = float(row.get("underlying_price") or float("nan"))
        strike = float(row.get("strike") or float("nan"))
        if not (math.isfinite(vol) and 0.0 < vol < 5.0 and math.isfinite(spot) and spot > 0.0 and math.isfinite(strike)):
            out_rows.append(_nan_result(row, str(row.get("greeks_engine", "")), "invalid_input"))
            continue

        tau = _tau_years(row.get("expiration"), row.get("asof_ts"))
        try:
            row_rate = float(rate_curve.rate(tau)) if rate_curve is not None else float(rate)
        except Exception as exc:
            out_rows.append(_nan_result(row, str(row.get("greeks_engine", "")), f"rate_curve_error:{exc}"))
            continue

        projected_divs = _projected_divs_for_row(row, tau=tau, dividend_source=dividend_source)
        row["tau_years"] = tau
        row["rate_used"] = row_rate
        row["dividend_used"] = _sum_dividends(projected_divs)
        div_policy = str(row.get("dividend_policy", "")).lower()
        use_node_event = div_policy == "node_event_exact"
        use_escrow = div_policy == "escrowed"
        if not div_policy:
            use_node_event = int(row.get("days_to_expiry", 0)) < 31
            use_escrow = not use_node_event

        contract = AmericanContract(
            spot=spot,
            strike=strike,
            rate=row_rate,
            dividend=(0.0 if (use_node_event or use_escrow) else dividend),
            tau=tau,
            is_call=str(row.get("option_type", "")).lower() == "call",
        )
        engine = str(row.get("greeks_engine", "")).lower()

        try:
            if engine == "crank_nicolson_fdm":
                out_rows.append(
                    _fdm_result(
                        row,
                        contract,
                        vol,
                        scheme=fdm_scheme,
                        divs=projected_divs if use_node_event else [],
                        backend=fdm_backend,
                        runtime_mode=runtime_mode,
                    )
                )
            elif engine == "binomial_richardson":
                out_rows.append(
                    _tree_result(
                        row,
                        contract,
                        vol,
                        steps=tree_steps,
                        divs=projected_divs if use_node_event else [],
                    )
                )
            elif engine == "bjerksund_stensland":
                out_rows.append(_bs2002_result(row, contract, vol, divs=projected_divs if use_escrow else []))
            elif engine == "rim":
                out_rows.append(
                    _rim_result(
                        row,
                        contract,
                        vol,
                        rim_nodes=rim_nodes,
                        use_escrow=use_escrow,
                        divs=projected_divs,
                    )
                )
            elif engine == "laplace_transform_zhu":
                out_rows.append(
                    _laplace_result(
                        row,
                        contract,
                        vol,
                        laplace_m=laplace_m,
                        use_escrow=use_escrow,
                        divs=projected_divs,
                    )
                )
            else:
                out_rows.append(_nan_result(row, engine, "unknown_engine"))
        except Exception as exc:
            out_rows.append(_nan_result(row, engine, str(exc)))

    if not out_rows:
        return _empty_greeks()
    return pl.DataFrame(out_rows).select(ROUTED_GREEKS_COLUMNS)

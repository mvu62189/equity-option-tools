from __future__ import annotations

import math
from datetime import date, datetime, timezone

import polars as pl

from .dividends import DividendEvent, escrowed_spot
from .models import AmericanContract
from .o4o5_engines import BjerksundStenslandEngine, estimate_luba_rim_eep_with_diagnostics

SUMMARY_COLUMNS = [
    "expiration",
    "winner_model",
    "bjerksund_error",
    "luba_error",
    "bjerksund_rmse",
    "luba_rmse",
    "winner_gap",
    "pairs",
    "tau_years",
]

DETAIL_COLUMNS = [
    "expiration",
    "strike",
    "spot",
    "tau_years",
    "model",
    "parity_error",
    "relative_error",
    "call_eur",
    "put_eur",
    "parity_rhs",
]

SOLVER_DIAG_COLUMNS = [
    "expiration",
    "model_id",
    "converged",
    "iterations",
    "sse_final",
    "durrleman_pass",
    "params",
]


def _empty_summary() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "expiration": [],
            "winner_model": [],
            "bjerksund_error": [],
            "luba_error": [],
            "bjerksund_rmse": [],
            "luba_rmse": [],
            "winner_gap": [],
            "pairs": [],
            "tau_years": [],
        }
    )


def _empty_detail() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "expiration": [],
            "strike": [],
            "spot": [],
            "tau_years": [],
            "model": [],
            "parity_error": [],
            "relative_error": [],
            "call_eur": [],
            "put_eur": [],
            "parity_rhs": [],
        }
    )


def _empty_solver_diag() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "expiration": [],
            "model_id": [],
            "converged": [],
            "iterations": [],
            "sse_final": [],
            "durrleman_pass": [],
            "params": [],
        }
    )


def _aggregate_solver_diag(rows: list[dict]) -> pl.DataFrame:
    if not rows:
        return _empty_solver_diag()

    grouped: dict[tuple[date, str], list[dict]] = {}
    for row in rows:
        key = (row["expiration"], row["model_id"])
        grouped.setdefault(key, []).append(row)

    out_rows: list[dict] = []
    for (expiration, model_id), vals in grouped.items():
        n = len(vals)
        converged_count = sum(1 for v in vals if bool(v["converged"]))
        iterations_total = sum(int(v["iterations"]) for v in vals)
        sse_vals = [float(v["sse_final"]) for v in vals if math.isfinite(float(v["sse_final"]))]
        sse_mean = float(sum(sse_vals) / len(sse_vals)) if sse_vals else float("inf")

        numeric_params: dict[str, list[float]] = {}
        for v in vals:
            params = v.get("params", {})
            if isinstance(params, dict):
                for k, x in params.items():
                    try:
                        xf = float(x)
                    except Exception:
                        continue
                    if math.isfinite(xf):
                        numeric_params.setdefault(k, []).append(xf)
        agg_params = {k: float(sum(xs) / len(xs)) for k, xs in numeric_params.items() if xs}
        agg_params["count"] = float(n)
        agg_params["converged_ratio"] = float(converged_count / max(n, 1))

        out_rows.append(
            {
                "expiration": expiration,
                "model_id": model_id,
                "converged": converged_count == n,
                "iterations": iterations_total,
                "sse_final": sse_mean,
                "durrleman_pass": True,
                "params": agg_params,
            }
        )

    return pl.DataFrame(out_rows).select(SOLVER_DIAG_COLUMNS).sort(["expiration", "model_id"])


def _tau_years(expiration: date, asof: datetime) -> float:
    exp_dt = datetime(expiration.year, expiration.month, expiration.day, 16, 0, 0, tzinfo=timezone.utc)
    if asof.tzinfo is None:
        asof = asof.replace(tzinfo=timezone.utc)
    seconds = max((exp_dt - asof).total_seconds(), 60.0)
    return seconds / (365.25 * 24.0 * 3600.0)


def _mid_price(bid: float, ask: float, last: float | None) -> float:
    if last is not None and math.isfinite(last) and last > 0:
        return float(last)
    if bid > 0 and ask > 0:
        return float((bid + ask) / 2.0)
    return float(max(bid, ask, 0.0))


def _pair_sigma(call_row: dict, put_row: dict) -> float:
    c_iv = float(call_row.get("implied_vol_input", call_row.get("iv_ref", call_row.get("implied_vol_vendor", 0.0))) or 0.0)
    p_iv = float(put_row.get("implied_vol_input", put_row.get("iv_ref", put_row.get("implied_vol_vendor", 0.0))) or 0.0)
    vals = [v for v in (c_iv, p_iv) if v > 1e-6 and math.isfinite(v)]
    if not vals:
        return 0.2
    return float(sum(vals) / len(vals))


def _estimate_eep(model: str, option_type: str, spot: float, strike: float, tau: float, price: float) -> float:
    moneyness = abs(math.log(max(spot, 1e-8) / max(strike, 1e-8)))
    root_tau = math.sqrt(max(tau, 1e-8))
    base = max(price, 0.0)

    # MVP heuristic coefficients. Replace with real O4 engines in phase 2.
    if model == "bjerksund_stensland":
        premium = base * (0.008 + 0.02 * root_tau) + 0.02 * moneyness
    elif model == "luba":
        premium = base * (0.006 + 0.016 * root_tau) + 0.015 * moneyness
    else:
        premium = 0.0

    if option_type == "put":
        premium *= 1.10
    return max(0.0, min(premium, base * 0.95))


def _select_strikes(common_strikes: list[float], spot: float, max_pairs: int) -> list[float]:
    if max_pairs <= 0 or len(common_strikes) <= max_pairs:
        return common_strikes
    closest = sorted(common_strikes, key=lambda x: abs(x - spot))[:max_pairs]
    return sorted(closest)


def _parity_rhs(spot: float, strike: float, rate: float, dividend: float, tau: float) -> float:
    return spot * math.exp(-dividend * tau) - strike * math.exp(-rate * tau)


def evaluate_parity_diagnostics(
    frame: pl.DataFrame,
    rate: float = 0.04,
    dividend: float = 0.0,
    eep_mode: str = "hybrid",
    max_pairs: int = 40,
    tree_steps: int = 120,
    luba_method: str = "luba_2pt",
    rim_nodes: int = 100,
    divs: list[DividendEvent] | tuple[DividendEvent, ...] = (),
    return_solver_diagnostics: bool = False,
) -> tuple[pl.DataFrame, pl.DataFrame] | tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    needed = {"expiration", "option_type", "strike", "bid", "ask", "underlying_price", "asof_ts", "last"}
    if frame.is_empty() or not needed.issubset(frame.columns):
        if return_solver_diagnostics:
            return _empty_summary(), _empty_detail(), _empty_solver_diag()
        return _empty_summary(), _empty_detail()

    rows = frame.to_dicts()
    by_expiry: dict[date, list[dict]] = {}
    for row in rows:
        exp = row["expiration"]
        by_expiry.setdefault(exp, []).append(row)

    summary_rows: list[dict] = []
    detail_rows: list[dict] = []
    solver_diag_rows: list[dict] = []

    bjerksund_engine = BjerksundStenslandEngine(steps=tree_steps)

    for expiration, exp_rows in by_expiry.items():
        calls: dict[float, dict] = {}
        puts: dict[float, dict] = {}
        for row in exp_rows:
            option_type = str(row["option_type"]).lower()
            strike = float(row["strike"])
            if option_type == "call":
                calls[strike] = row
            elif option_type == "put":
                puts[strike] = row

        common_strikes = sorted(set(calls.keys()) & set(puts.keys()))
        if not common_strikes:
            continue

        sample_row = calls[common_strikes[0]]
        asof = sample_row["asof_ts"]
        asof_ts = asof if isinstance(asof, datetime) else datetime.now(timezone.utc)
        tau = _tau_years(expiration, asof_ts)
        spot_ref = float(sample_row["underlying_price"])
        selected_strikes = _select_strikes(common_strikes, spot_ref, max_pairs)

        # Policy: <= 1 month uses node-event exact discrete-div mode (no escrow).
        # >= 1 month uses escrowed spot transform with q=0.
        use_escrow = tau >= (31.0 / 365.25)
        use_node_event_divs = (not use_escrow) and bool(divs)

        errors_by_model: dict[str, list[float]] = {"bjerksund_stensland": [], "luba": []}
        for strike in selected_strikes:
            call_row = calls[strike]
            put_row = puts[strike]
            spot = float(call_row["underlying_price"])
            sigma = _pair_sigma(call_row, put_row)

            if use_escrow:
                q_model = 0.0
                spot_model = escrowed_spot(spot, rate=rate, tau=tau, divs=divs)
            elif use_node_event_divs:
                q_model = 0.0
                spot_model = spot
            else:
                q_model = 0.0
                spot_model = spot

            call_price = _mid_price(float(call_row["bid"]), float(call_row["ask"]), call_row.get("last"))
            put_price = _mid_price(float(put_row["bid"]), float(put_row["ask"]), put_row.get("last"))
            rhs = _parity_rhs(spot_model, strike, rate, q_model, tau)

            for model in errors_by_model:
                if model == "bjerksund_stensland" and eep_mode in {"hybrid", "model"}:
                    call_diag = bjerksund_engine.estimate_eep(
                        market_price=call_price,
                        contract=AmericanContract(
                            spot=spot_model,
                            strike=strike,
                            rate=rate,
                            dividend=q_model,
                            tau=tau,
                            is_call=True,
                        ),
                        divs=divs if use_node_event_divs else (),
                        force_zero_q=use_node_event_divs,
                    )
                    put_diag = bjerksund_engine.estimate_eep(
                        market_price=put_price,
                        contract=AmericanContract(
                            spot=spot_model,
                            strike=strike,
                            rate=rate,
                            dividend=q_model,
                            tau=tau,
                            is_call=False,
                        ),
                        divs=divs if use_node_event_divs else (),
                        force_zero_q=use_node_event_divs,
                    )
                    call_eep = call_diag.eep
                    put_eep = put_diag.eep
                elif model == "luba" and eep_mode in {"hybrid", "model"}:
                    call_eep, call_solver_diag = estimate_luba_rim_eep_with_diagnostics(
                        market_price=call_price,
                        option_type="call",
                        spot=spot,
                        strike=strike,
                        tau=tau,
                        rate=rate,
                        dividend=q_model,
                        sigma=sigma,
                        method=luba_method,
                        use_escrow=use_escrow,
                        divs=divs,
                        rim_nodes=rim_nodes,
                    )
                    put_eep, put_solver_diag = estimate_luba_rim_eep_with_diagnostics(
                        market_price=put_price,
                        option_type="put",
                        spot=spot,
                        strike=strike,
                        tau=tau,
                        rate=rate,
                        dividend=q_model,
                        sigma=sigma,
                        method=luba_method,
                        use_escrow=use_escrow,
                        divs=divs,
                        rim_nodes=rim_nodes,
                    )
                    solver_diag_rows.append(
                        {
                            "expiration": expiration,
                            "model_id": call_solver_diag.model_id,
                            "converged": call_solver_diag.converged,
                            "iterations": call_solver_diag.iterations,
                            "sse_final": call_solver_diag.sse_final,
                            "params": call_solver_diag.params,
                        }
                    )
                    solver_diag_rows.append(
                        {
                            "expiration": expiration,
                            "model_id": put_solver_diag.model_id,
                            "converged": put_solver_diag.converged,
                            "iterations": put_solver_diag.iterations,
                            "sse_final": put_solver_diag.sse_final,
                            "params": put_solver_diag.params,
                        }
                    )
                else:
                    call_eep = _estimate_eep(model, "call", spot, strike, tau, call_price)
                    put_eep = _estimate_eep(model, "put", spot, strike, tau, put_price)
                call_eur = max(call_price - call_eep, 1e-8)
                put_eur = max(put_price - put_eep, 1e-8)
                parity_err = abs((call_eur - put_eur) - rhs)
                rel = parity_err / max(abs(rhs), 1.0)
                errors_by_model[model].append(parity_err)
                detail_rows.append(
                    {
                        "expiration": expiration,
                        "strike": strike,
                        "spot": spot,
                        "tau_years": tau,
                        "model": model,
                        "parity_error": parity_err,
                        "relative_error": rel,
                        "call_eur": call_eur,
                        "put_eur": put_eur,
                        "parity_rhs": rhs,
                    }
                )

        b_errors = errors_by_model["bjerksund_stensland"]
        l_errors = errors_by_model["luba"]
        b_mean = sum(b_errors) / len(b_errors)
        l_mean = sum(l_errors) / len(l_errors)
        b_rmse = math.sqrt(sum(x * x for x in b_errors) / len(b_errors))
        l_rmse = math.sqrt(sum(x * x for x in l_errors) / len(l_errors))
        winner = "bjerksund_stensland" if b_mean <= l_mean else "luba"

        summary_rows.append(
            {
                "expiration": expiration,
                "winner_model": winner,
                "bjerksund_error": b_mean,
                "luba_error": l_mean,
                "bjerksund_rmse": b_rmse,
                "luba_rmse": l_rmse,
                "winner_gap": abs(b_mean - l_mean),
                "pairs": len(selected_strikes),
                "tau_years": tau,
            }
        )

    if not summary_rows:
        if return_solver_diagnostics:
            return _empty_summary(), _empty_detail(), _empty_solver_diag()
        return _empty_summary(), _empty_detail()

    summary = pl.DataFrame(summary_rows).select(SUMMARY_COLUMNS).sort("expiration")
    detail = pl.DataFrame(detail_rows).select(DETAIL_COLUMNS).sort(["expiration", "strike", "model"])
    if return_solver_diagnostics:
        solver_diag = _aggregate_solver_diag(solver_diag_rows)
        return summary, detail, solver_diag
    return summary, detail


def evaluate_parity_by_expiry(
    frame: pl.DataFrame,
    rate: float = 0.04,
    dividend: float = 0.0,
) -> pl.DataFrame:
    summary, _detail = evaluate_parity_diagnostics(frame, rate=rate, dividend=dividend)
    return summary


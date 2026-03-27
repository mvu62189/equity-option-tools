from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import polars as pl
from scipy.interpolate import PchipInterpolator

from flow_core.quant.bs import BSInput, implied_vol_euro_bs
from flow_core.quant.market_inputs import HybridDividendSource, TBillRateCurve
from flow_core.quant.models import AmericanContract
from flow_core.quant.o4o5_engines import BjerksundStenslandEngine
from flow_core.quant.routed_greeks import _projected_divs_for_row

KEY_COLUMNS = ["symbol", "contract_symbol", "expiration", "option_type", "strike"]


@dataclass(slots=True)
class QuoteQualityBundle:
    points: pl.DataFrame
    calibration_input: pl.DataFrame
    eligible_frame: pl.DataFrame


def _empty_bundle() -> QuoteQualityBundle:
    return QuoteQualityBundle(points=pl.DataFrame(), calibration_input=pl.DataFrame(), eligible_frame=pl.DataFrame())


def _to_float(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def _is_finite(value: Any) -> bool:
    return math.isfinite(_to_float(value))


def _to_utc_datetime(value: datetime | date | None) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, 16, 0, 0, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _tau_years(expiration: date | datetime | None, asof: datetime | date | None) -> float:
    exp_dt = _to_utc_datetime(expiration)
    asof_dt = _to_utc_datetime(asof)
    seconds = max((exp_dt - asof_dt).total_seconds(), 60.0)
    return max(seconds / (365.25 * 24.0 * 3600.0), 1.0 / 365.0)


def _market_mid(bid: float, ask: float) -> float:
    if math.isfinite(bid) and math.isfinite(ask) and bid > 0.0 and ask > 0.0:
        return 0.5 * (bid + ask)
    return float("nan")


def _duplicate_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("contract_symbol"),
        row.get("expiration"),
        row.get("option_type"),
        row.get("strike"),
        row.get("bid"),
        row.get("ask"),
        row.get("last"),
        row.get("volume"),
        row.get("open_interest"),
        row.get("asof_ts"),
    )


def _group_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("symbol"),
        row.get("contract_symbol"),
        row.get("expiration"),
        row.get("option_type"),
        row.get("strike"),
    )


def _pava_non_decreasing(values: np.ndarray, weights: np.ndarray | None = None) -> np.ndarray:
    if values.size == 0:
        return values.astype(np.float64, copy=True)
    y = np.asarray(values, dtype=np.float64)
    w = np.asarray(weights if weights is not None else np.ones_like(y), dtype=np.float64)
    blocks_y = y.astype(float).tolist()
    blocks_w = w.astype(float).tolist()
    blocks_start = list(range(len(y)))
    i = 0
    while i < len(blocks_y) - 1:
        if blocks_y[i] <= blocks_y[i + 1] + 1e-12:
            i += 1
            continue
        total_w = blocks_w[i] + blocks_w[i + 1]
        merged_y = (blocks_y[i] * blocks_w[i] + blocks_y[i + 1] * blocks_w[i + 1]) / max(total_w, 1e-12)
        blocks_y[i] = merged_y
        blocks_w[i] = total_w
        del blocks_y[i + 1]
        del blocks_w[i + 1]
        del blocks_start[i + 1]
        if i > 0:
            i -= 1
    out = np.empty_like(y)
    ends = blocks_start[1:] + [len(y)]
    for start, end, value in zip(blocks_start, ends, blocks_y, strict=False):
        out[start:end] = value
    return out


def _option_sign(option_type: str) -> float:
    return -1.0 if option_type == "call" else 1.0


def _dual_delta_from_price(prices: np.ndarray, strikes: np.ndarray, option_type: str) -> np.ndarray:
    if prices.size == 0:
        return np.asarray([], dtype=np.float64)
    if prices.size == 1:
        return np.asarray([0.0], dtype=np.float64)
    if strikes.size != np.unique(strikes).size or np.any(np.diff(strikes) <= 0.0):
        return np.full_like(prices, np.nan, dtype=np.float64)
    d_price = np.gradient(prices, strikes)
    return _option_sign(option_type) * d_price


def _atm_weight(log_moneyness: float) -> float:
    if not math.isfinite(log_moneyness):
        return 0.0
    dist = abs(log_moneyness)
    if dist <= 0.10:
        return 1.0
    if dist >= 0.35:
        return 0.20
    return max(0.20, 1.0 - ((dist - 0.10) / 0.25) * 0.80)


def _normalize_corridor_weight(widths: np.ndarray) -> np.ndarray:
    if widths.size == 0:
        return np.asarray([], dtype=np.float64)
    inv = 1.0 / np.maximum(widths, 1e-6)
    mean_inv = float(np.mean(inv)) if inv.size else 1.0
    scaled = inv / max(mean_inv, 1e-6)
    return np.clip(scaled, 0.25, 4.0)


def _price_band_to_reference(
    strikes: np.ndarray,
    price_bid: np.ndarray,
    price_ask: np.ndarray,
    option_type: str,
    *,
    atm_index: int,
    discount_cap: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool]:
    if strikes.size < 3:
        mid = 0.5 * (price_bid + price_ask)
        q_mid = _dual_delta_from_price(mid, strikes, option_type)
        second = np.gradient(np.gradient(mid, strikes), strikes) if strikes.size > 1 else np.zeros_like(mid)
        return mid, q_mid, second, False

    mid = 0.5 * (price_bid + price_ask)
    q_bid = _dual_delta_from_price(price_bid, strikes, option_type)
    q_ask = _dual_delta_from_price(price_ask, strikes, option_type)
    q_lo = np.minimum(q_bid, q_ask)
    q_hi = np.maximum(q_bid, q_ask)
    q_raw = _dual_delta_from_price(mid, strikes, option_type)
    q_raw = np.clip(q_raw, np.maximum(q_lo, 0.0), np.minimum(q_hi, discount_cap))
    q_iso = _pava_non_decreasing(q_raw)
    q_ref = np.clip(q_iso, np.maximum(q_lo, 0.0), np.minimum(q_hi, discount_cap))
    q_ref = _pava_non_decreasing(q_ref)

    price_ref = np.full_like(mid, np.nan, dtype=np.float64)
    anchor = float(np.clip(mid[atm_index], price_bid[atm_index], price_ask[atm_index]))
    price_ref[atm_index] = anchor
    for idx in range(atm_index + 1, strikes.size):
        slope = 0.5 * (q_ref[idx - 1] + q_ref[idx])
        delta_k = strikes[idx] - strikes[idx - 1]
        price_ref[idx] = price_ref[idx - 1] + (_option_sign(option_type) * slope * delta_k)
    for idx in range(atm_index - 1, -1, -1):
        slope = 0.5 * (q_ref[idx] + q_ref[idx + 1])
        delta_k = strikes[idx + 1] - strikes[idx]
        price_ref[idx] = price_ref[idx + 1] - (_option_sign(option_type) * slope * delta_k)

    price_ref = np.clip(price_ref, price_bid, price_ask)
    spline = PchipInterpolator(strikes, price_ref, extrapolate=False)
    price_smooth = np.asarray(spline(strikes), dtype=np.float64)
    first = np.asarray(spline.derivative(1)(strikes), dtype=np.float64)
    second = np.asarray(spline.derivative(2)(strikes), dtype=np.float64)
    q_smooth = _option_sign(option_type) * first
    q_smooth = np.clip(q_smooth, 0.0, discount_cap)
    shape_fail = bool(np.any(second < -1e-6))
    return price_smooth, q_smooth, second, shape_fail


def _prepare_row(
    row: dict[str, Any],
    *,
    rate: float,
    rate_curve: TBillRateCurve | None,
    dividend_source: HybridDividendSource | None,
    american_iv_engine: BjerksundStenslandEngine,
) -> dict[str, Any]:
    out = dict(row)
    bid = _to_float(row.get("bid"))
    ask = _to_float(row.get("ask"))
    strike = _to_float(row.get("strike"))
    spot = _to_float(row.get("underlying_price"))
    option_type = str(row.get("option_type", "")).lower()
    tau = _tau_years(row.get("expiration"), row.get("asof_ts"))
    asof_ts = _to_utc_datetime(row.get("asof_ts"))
    try:
        row_rate = float(rate_curve.rate(tau)) if rate_curve is not None else float(rate)
    except Exception:
        row_rate = float(rate)
    projected_divs = _projected_divs_for_row(row, tau=tau, dividend_source=dividend_source)
    use_divs = projected_divs if str(row.get("dividend_policy", "")).lower() in {"node_event_exact", "escrowed"} else []
    force_zero_q = bool(use_divs)

    log_moneyness = float("nan")
    if strike > 0.0 and spot > 0.0:
        log_moneyness = math.log(strike / spot)

    out.update(
        {
            "market_mid": _market_mid(bid, ask),
            "tau_years": tau,
            "rate_used": row_rate,
            "dividend_used": float(sum(float(div.amount) for div in use_divs if float(div.amount) > 0.0)),
            "log_moneyness": log_moneyness,
            "atm_distance": abs(log_moneyness) if math.isfinite(log_moneyness) else float("nan"),
            "one_sided_market": bool(bid <= 0.0 or ask <= 0.0),
            "crossed_market": bool(_is_finite(bid) and _is_finite(ask) and ask < bid),
            "nonfinite_market": not all(_is_finite(x) for x in (bid, ask, strike, spot)),
            "duplicate_conflict": False,
            "exact_duplicate": False,
            "drop_reason": "",
            "eligible_prestrip": False,
            "eligible": False,
            "iv_bid": float("nan"),
            "iv_ask": float("nan"),
            "iv_ref": float("nan"),
            "vendor_iv_ref": _to_float(row.get("implied_vol_vendor")),
            "euro_price_bid": float("nan"),
            "euro_price_ask": float("nan"),
            "euro_price_ref": float("nan"),
            "dual_delta_bid": float("nan"),
            "dual_delta_ask": float("nan"),
            "dual_delta_ref": float("nan"),
            "price_second_derivative_ref": float("nan"),
            "corridor_tightness": float("nan"),
            "corridor_width": float("nan"),
            "weight_uniform": 1.0,
            "weight_atm": _atm_weight(log_moneyness),
            "weight_corridor_tightness": float("nan"),
            "weight_atm_corridor_tightness": float("nan"),
            "strip_shape_fail": False,
            "strip_shape_reason": "",
            "fit_region": "unknown",
            "is_atm_blend": False,
            "blend_source": "",
            "eligible_for_fit": False,
            "excluded_from_fit_reason": "",
        }
    )
    if out["nonfinite_market"]:
        out["drop_reason"] = "nonfinite_market"
        return out
    if out["one_sided_market"]:
        out["drop_reason"] = "one_sided_market"
        return out
    if out["crossed_market"]:
        out["drop_reason"] = "crossed_market"
        return out
    if option_type not in {"call", "put"} or strike <= 0.0 or spot <= 0.0 or tau <= 0.0:
        out["drop_reason"] = "structural_invalid"
        return out

    contract = AmericanContract(
        spot=spot,
        strike=strike,
        rate=row_rate,
        dividend=0.0 if force_zero_q else _to_float(row.get("dividend", 0.0), 0.0),
        tau=tau,
        is_call=option_type == "call",
    )
    bid_diag = american_iv_engine.estimate_eep(bid, contract, divs=use_divs, force_zero_q=force_zero_q)
    ask_diag = american_iv_engine.estimate_eep(ask, contract, divs=use_divs, force_zero_q=force_zero_q)
    if not (bid_diag.success and ask_diag.success):
        out["drop_reason"] = "american_iv_inversion_failed"
        return out

    out["iv_bid"] = float(bid_diag.implied_vol)
    out["iv_ask"] = float(ask_diag.implied_vol)
    out["euro_price_bid"] = float(bid_diag.european_price)
    out["euro_price_ask"] = float(ask_diag.european_price)
    if not (
        _is_finite(out["iv_bid"])
        and _is_finite(out["iv_ask"])
        and _is_finite(out["euro_price_bid"])
        and _is_finite(out["euro_price_ask"])
        and out["euro_price_ask"] >= out["euro_price_bid"]
    ):
        out["drop_reason"] = "euro_strip_invalid"
        return out
    out["corridor_width"] = max(float(out["iv_ask"]) - float(out["iv_bid"]), 1e-6)
    out["eligible_prestrip"] = True
    return out


def _apply_duplicate_policy(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, ...], list[int]] = {}
    for idx, row in enumerate(rows):
        grouped.setdefault(_group_key(row), []).append(idx)
    for indices in grouped.values():
        if len(indices) <= 1:
            continue
        first_signature = _duplicate_signature(rows[indices[0]])
        if all(_duplicate_signature(rows[idx]) == first_signature for idx in indices[1:]):
            for dup_idx in indices[1:]:
                rows[dup_idx]["exact_duplicate"] = True
                rows[dup_idx]["drop_reason"] = "exact_duplicate"
        else:
            for dup_idx in indices:
                rows[dup_idx]["duplicate_conflict"] = True
                rows[dup_idx]["drop_reason"] = "duplicate_conflict"
    return rows


def _apply_strip_reference(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_group: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = (row.get("expiration"), row.get("option_type"))
        by_group.setdefault(key, []).append(row)

    for group_rows in by_group.values():
        eligible = [
            row
            for row in group_rows
            if bool(row.get("eligible_prestrip"))
            and not bool(row.get("duplicate_conflict"))
            and not bool(row.get("exact_duplicate"))
        ]
        if not eligible:
            continue
        eligible.sort(key=lambda item: _to_float(item.get("strike")))
        strikes = np.asarray([_to_float(row.get("strike")) for row in eligible], dtype=np.float64)
        if strikes.size != np.unique(strikes).size or np.any(np.diff(strikes) <= 0.0):
            for row in eligible:
                row["strip_shape_fail"] = True
                row["strip_shape_reason"] = "duplicate_strike_grid"
                if not row.get("drop_reason"):
                    row["drop_reason"] = "duplicate_strike_grid"
                row["eligible"] = False
            continue
        euro_bid = np.asarray([_to_float(row.get("euro_price_bid")) for row in eligible], dtype=np.float64)
        euro_ask = np.asarray([_to_float(row.get("euro_price_ask")) for row in eligible], dtype=np.float64)
        option_type = str(eligible[0].get("option_type", "call")).lower()
        atm_idx = int(np.argmin(np.asarray([abs(_to_float(row.get("log_moneyness"))) for row in eligible], dtype=np.float64)))
        tau = _to_float(eligible[0].get("tau_years"), 0.0)
        rate = _to_float(eligible[0].get("rate_used"), 0.0)
        discount_cap = max(math.exp(-rate * max(tau, 0.0)), 1e-6)

        dual_bid = _dual_delta_from_price(euro_bid, strikes, option_type)
        dual_ask = _dual_delta_from_price(euro_ask, strikes, option_type)
        price_ref, dual_ref, second_ref, shape_fail = _price_band_to_reference(
            strikes,
            euro_bid,
            euro_ask,
            option_type,
            atm_index=atm_idx,
            discount_cap=discount_cap,
        )
        corridor_width = np.maximum(
            np.asarray([_to_float(row.get("iv_ask")) - _to_float(row.get("iv_bid")) for row in eligible], dtype=np.float64),
            1e-6,
        )
        corridor_weights = _normalize_corridor_weight(corridor_width)

        for idx, row in enumerate(eligible):
            mid_euro = 0.5 * (_to_float(row.get("euro_price_bid")) + _to_float(row.get("euro_price_ask")))
            iv_ref = implied_vol_euro_bs(
                float(price_ref[idx]),
                BSInput(
                    spot=_to_float(row.get("underlying_price")),
                    strike=_to_float(row.get("strike")),
                    rate=_to_float(row.get("rate_used")),
                    dividend=0.0,
                    tau=_to_float(row.get("tau_years")),
                    vol=0.2,
                    is_call=option_type == "call",
                ),
            )
            if not _is_finite(iv_ref):
                iv_ref = implied_vol_euro_bs(
                    float(mid_euro),
                    BSInput(
                        spot=_to_float(row.get("underlying_price")),
                        strike=_to_float(row.get("strike")),
                        rate=_to_float(row.get("rate_used")),
                        dividend=0.0,
                        tau=_to_float(row.get("tau_years")),
                        vol=0.2,
                        is_call=option_type == "call",
                    ),
                )
                if _is_finite(iv_ref):
                    price_ref[idx] = mid_euro
            row["dual_delta_bid"] = float(dual_bid[idx])
            row["dual_delta_ask"] = float(dual_ask[idx])
            row["dual_delta_ref"] = float(dual_ref[idx])
            row["price_second_derivative_ref"] = float(second_ref[idx])
            row["euro_price_ref"] = float(price_ref[idx])
            row["iv_ref"] = float(iv_ref)
            row["corridor_tightness"] = float(1.0 / corridor_width[idx])
            row["corridor_width"] = float(corridor_width[idx])
            row["weight_corridor_tightness"] = float(corridor_weights[idx])
            row["weight_atm_corridor_tightness"] = float(corridor_weights[idx] * _to_float(row.get("weight_atm"), 0.0))
            row["strip_shape_fail"] = bool(shape_fail or not _is_finite(iv_ref) or second_ref[idx] < -1e-6)
            if row["strip_shape_fail"] and not row["drop_reason"]:
                row["strip_shape_reason"] = "dual_delta_or_convexity_invalid"
                row["drop_reason"] = "strip_shape_fail"
            row["eligible"] = bool(row["eligible_prestrip"] and not row["strip_shape_fail"] and _is_finite(iv_ref))

    return rows


def _forward_price(row: dict[str, Any]) -> float:
    spot = _to_float(row.get("underlying_price"))
    rate = _to_float(row.get("rate_used"), 0.0)
    tau = _to_float(row.get("tau_years"), 0.0)
    if not (_is_finite(spot) and spot > 0.0 and _is_finite(rate) and _is_finite(tau) and tau > 0.0):
        return float("nan")
    return float(spot * math.exp(rate * tau))


def _fit_region(row: dict[str, Any], forward: float, atm_strikes: set[float]) -> str:
    strike = _to_float(row.get("strike"))
    option_type = str(row.get("option_type", "")).lower()
    if not math.isfinite(strike) or not math.isfinite(forward):
        return "unknown"
    if any(abs(strike - atm) <= 1e-9 for atm in atm_strikes):
        return "atm_blend_candidate"
    if option_type == "put":
        return "otm_put" if strike < forward else "itm_put"
    if option_type == "call":
        return "otm_call" if strike > forward else "itm_call"
    return "unknown"


def _blend_atm_rows(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = [row for row in rows if _is_finite(row.get("iv_bid")) and _is_finite(row.get("iv_ask"))]
    if not valid:
        return None
    widths = np.asarray([max(_to_float(row.get("corridor_width"), 1e-6), 1e-6) for row in valid], dtype=np.float64)
    weights = 1.0 / widths
    weights = weights / max(float(np.sum(weights)), 1e-12)
    bid = float(np.sum(weights * np.asarray([_to_float(row.get("iv_bid")) for row in valid], dtype=np.float64)))
    ask = float(np.sum(weights * np.asarray([_to_float(row.get("iv_ask")) for row in valid], dtype=np.float64)))
    ref_values = np.asarray([_to_float(row.get("iv_ref")) for row in valid], dtype=np.float64)
    ref = float(np.sum(weights * np.where(np.isfinite(ref_values), ref_values, 0.5 * (bid + ask))))
    base = dict(valid[0])
    base["iv_bid"] = min(bid, ask)
    base["iv_ask"] = max(bid, ask)
    base["iv_ref"] = float(np.clip(ref, base["iv_bid"], base["iv_ask"]))
    base["corridor_width"] = max(base["iv_ask"] - base["iv_bid"], 1e-6)
    base["corridor_tightness"] = 1.0 / base["corridor_width"]
    base["is_atm_blend"] = True
    base["blend_source"] = "+".join(sorted({str(row.get("option_type", "")) for row in valid}))
    base["fit_region"] = "atm_blend"
    base["eligible_for_fit"] = True
    base["excluded_from_fit_reason"] = ""
    base["surface_source"] = "surface"
    base["option_type"] = "surface"
    base["contract_symbol"] = f"{base.get('symbol', '')}:{base.get('expiration', '')}:{base.get('strike', '')}:surface"
    return base


def _build_calibration_input(points: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    if points.is_empty():
        return points, pl.DataFrame()
    required = {"expiration", "strike", "underlying_price", "iv_ref", "iv_bid", "iv_ask", "weight_atm"}
    if not required.issubset(points.columns):
        return points, pl.DataFrame()
    rows = points.to_dicts()
    fit_rows: list[dict[str, Any]] = []
    by_expiry: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        by_expiry.setdefault(row.get("expiration"), []).append(row)

    for expiry_rows in by_expiry.values():
        candidates = [
            row
            for row in expiry_rows
            if bool(row.get("eligible"))
            and not bool(row.get("duplicate_conflict"))
            and not bool(row.get("exact_duplicate"))
            and _is_finite(row.get("iv_bid"))
            and _is_finite(row.get("iv_ask"))
        ]
        if not candidates:
            continue
        option_types_present = {str(row.get("option_type", "")).lower() for row in candidates}
        forward = _forward_price(candidates[0])
        strike_values = sorted({_to_float(row.get("strike")) for row in candidates if _is_finite(row.get("strike"))})
        if not strike_values:
            continue
        lower = max((strike for strike in strike_values if strike <= forward), default=strike_values[0])
        upper = min((strike for strike in strike_values if strike >= forward), default=strike_values[-1])
        atm_strikes = {lower, upper} if abs(upper - lower) > 1e-9 else {lower}

        for row in expiry_rows:
            row["fit_region"] = _fit_region(row, forward, atm_strikes)
            row["eligible_for_fit"] = False
            if not row.get("drop_reason") and row["fit_region"].startswith("itm_"):
                row["excluded_from_fit_reason"] = "itm_diagnostic_only"
            elif row.get("drop_reason"):
                row["excluded_from_fit_reason"] = str(row.get("drop_reason"))

        if option_types_present >= {"call", "put"}:
            for atm_strike in sorted(atm_strikes):
                atm_rows = [row for row in candidates if abs(_to_float(row.get("strike")) - atm_strike) <= 1e-9]
                blended = _blend_atm_rows(atm_rows)
                if blended is not None:
                    fit_rows.append(blended)
                    for row in atm_rows:
                        row["eligible_for_fit"] = True
                        row["is_atm_blend"] = True
                        row["blend_source"] = blended["blend_source"]
                        row["fit_region"] = "atm_blend"

            for strike in strike_values:
                if any(abs(strike - atm) <= 1e-9 for atm in atm_strikes):
                    continue
                if strike < forward:
                    side_rows = [row for row in candidates if abs(_to_float(row.get("strike")) - strike) <= 1e-9 and str(row.get("option_type", "")).lower() == "put"]
                    fit_region = "otm_put"
                else:
                    side_rows = [row for row in candidates if abs(_to_float(row.get("strike")) - strike) <= 1e-9 and str(row.get("option_type", "")).lower() == "call"]
                    fit_region = "otm_call"
                if not side_rows:
                    continue
                chosen = min(side_rows, key=lambda row: _to_float(row.get("corridor_width"), float("inf")))
                chosen["eligible_for_fit"] = True
                chosen["fit_region"] = fit_region
                chosen["excluded_from_fit_reason"] = ""
                chosen["surface_source"] = "surface"
                fit_rows.append(dict(chosen))
        else:
            surface_source = next(iter(option_types_present), "surface")
            for row in candidates:
                row["eligible_for_fit"] = True
                row["excluded_from_fit_reason"] = ""
                row["surface_source"] = surface_source
                fit_rows.append(dict(row))

    if not fit_rows:
        return pl.DataFrame(rows), pl.DataFrame()

    aggs: list[pl.Expr] = [
        (pl.first("asof_ts") if "asof_ts" in points.columns else pl.lit(None)).alias("asof_ts"),
        (
            pl.first("trading_date")
            if "trading_date" in points.columns
            else pl.lit(None, dtype=pl.String)
        ).alias("trading_date"),
        (
            pl.first("snapshot_kind")
            if "snapshot_kind" in points.columns
            else pl.lit(None, dtype=pl.String)
        ).alias("snapshot_kind"),
        (
            pl.first("source_mode")
            if "source_mode" in points.columns
            else pl.lit(None, dtype=pl.String)
        ).alias("source_mode"),
        pl.mean("underlying_price").alias("underlying_price"),
        pl.mean("tau_years").alias("tau_years"),
        pl.mean("rate_used").alias("rate_used"),
        pl.mean("iv_ref").alias("implied_vol_input"),
        pl.mean("iv_bid").alias("iv_bid"),
        pl.mean("iv_ask").alias("iv_ask"),
        pl.mean("weight_uniform").alias("weight_uniform"),
        pl.mean("weight_atm").alias("weight_atm"),
        pl.mean("weight_corridor_tightness").alias("weight_corridor_tightness"),
        pl.mean("weight_atm_corridor_tightness").alias("weight_atm_corridor_tightness"),
        pl.first("fit_region").alias("fit_region"),
        pl.any("is_atm_blend").alias("is_atm_blend"),
        pl.first("blend_source").alias("blend_source"),
        pl.first("surface_source").alias("surface_source"),
        pl.len().alias("contracts"),
    ]
    fit_frame = pl.DataFrame(fit_rows)
    grouped = fit_frame.group_by(["symbol", "expiration", "strike"]).agg(*aggs).sort(["expiration", "strike"])
    grouped = grouped.with_columns(
        pl.lit("surface").alias("option_type"),
        pl.lit(True).alias("eligible_for_fit"),
    )
    return pl.DataFrame(rows), grouped


def build_quote_quality(
    frame: pl.DataFrame,
    *,
    rate: float = 0.04,
    rate_curve: TBillRateCurve | None = None,
    dividend_source: HybridDividendSource | None = None,
    american_iv_engine: BjerksundStenslandEngine | None = None,
) -> QuoteQualityBundle:
    if frame.is_empty():
        return _empty_bundle()

    engine = american_iv_engine or BjerksundStenslandEngine(steps=120)
    rows = [
        _prepare_row(
            row,
            rate=rate,
            rate_curve=rate_curve,
            dividend_source=dividend_source,
            american_iv_engine=engine,
        )
        for row in frame.to_dicts()
    ]
    rows = _apply_duplicate_policy(rows)
    rows = _apply_strip_reference(rows)
    points, calibration_input = _build_calibration_input(pl.DataFrame(rows))

    eligible_cols = KEY_COLUMNS + ["iv_ref", "weight_uniform", "weight_atm", "weight_corridor_tightness", "weight_atm_corridor_tightness"]
    eligible_frame = points.filter(pl.col("eligible") == True).select(
        [col for col in eligible_cols if col in points.columns]
    )
    return QuoteQualityBundle(points=points, calibration_input=calibration_input, eligible_frame=eligible_frame)

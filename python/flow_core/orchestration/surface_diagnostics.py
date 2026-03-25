from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
from typing import Any, Iterable

import polars as pl
from scipy.optimize import brentq

from flow_core.quant.bs import price_euro_bs
from flow_core.quant.models import BSInput

_EPS = 1e-12


@dataclass(slots=True)
class SurfaceDiagnosticsBundle:
    points: pl.DataFrame
    summary: pl.DataFrame


POINT_DIAGNOSTIC_COLUMNS = [
    "american_model_price",
    "model_implied_vol",
    "price_error_abs",
    "price_error_rel",
    "vol_error_abs",
    "vol_error_rel",
    "within_bid_ask",
    "bid_ask_width",
    "log_moneyness",
    "atm_distance",
    "is_negative_gamma",
    "delta_smoothness_violation",
    "calendar_total_variance",
    "calendar_violation",
]

SUMMARY_COLUMNS = [
    "rows",
    "groups",
    "expiry_count",
    "strike_count",
    "failure_count",
    "model_implied_vol_coverage",
    "price_rmse",
    "vol_rmse",
    "atm_mae",
    "wing_rmse",
    "within_bid_ask_count",
    "within_bid_ask_ratio",
    "american_within_bid_ask_ratio",
    "negative_gamma_count",
    "negative_gamma_ratio",
    "delta_smoothness_violation_count",
    "delta_smoothness_violation_ratio",
    "calendar_violation_count",
    "calendar_violation_ratio",
    "calendar_groups_checked",
    "one_sided_drop_count",
    "duplicate_conflict_count",
    "strip_shape_fail_count",
    "core_eligible_rows",
    "density_negative_count",
]


def _is_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except Exception:
        return False


def _to_float(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def _normalize_expiry(value: Any) -> datetime | date | str:
    if isinstance(value, (datetime, date)):
        return value
    if value is None:
        return ""
    return str(value)


def _sort_key(value: Any) -> tuple[int, float | str]:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return (0, float(value))
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return (1, float(dt.timestamp()))
    if isinstance(value, date):
        return (1, float(datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp()))
    if value is None:
        return (2, "")
    return (3, str(value))


def _candidate_value(row: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return value
    return None


def _bs_implied_vol(
    *,
    spot: float,
    strike: float,
    tau: float,
    rate: float,
    dividend: float,
    is_call: bool,
    target_price: float,
) -> float:
    if not all(_is_finite(v) for v in (spot, strike, tau, rate, dividend, target_price)):
        return float("nan")
    if spot <= 0.0 or strike <= 0.0 or tau <= 0.0 or target_price <= 0.0:
        return float("nan")

    def objective(vol: float) -> float:
        return (
            price_euro_bs(
                BSInput(
                    spot=spot,
                    strike=strike,
                    rate=rate,
                    dividend=dividend,
                    tau=tau,
                    vol=vol,
                    is_call=is_call,
                )
            ).price
            - target_price
        )

    try:
        low = 1e-6
        high = 4.0
        f_low = objective(low)
        f_high = objective(high)
        if not all(math.isfinite(x) for x in (f_low, f_high)) or f_low * f_high > 0:
            return float("nan")
        return float(brentq(objective, low, high, maxiter=100, xtol=1e-7))
    except Exception:
        return float("nan")


def _group_totals(frame: pl.DataFrame, columns: list[str]) -> dict[tuple[Any, ...], list[tuple[int, dict[str, Any]]]]:
    grouped: dict[tuple[Any, ...], list[tuple[int, dict[str, Any]]]] = {}
    for idx, row in enumerate(frame.to_dicts()):
        key = tuple(row.get(col) for col in columns)
        grouped.setdefault(key, []).append((idx, row))
    return grouped


def build_surface_point_diagnostics(
    frame: pl.DataFrame,
    *,
    strike_col: str = "strike",
    option_type_col: str = "option_type",
    expiry_col: str = "expiration",
    tau_col: str = "tau_years",
    underlying_col: str = "underlying_price",
    bid_col: str = "bid",
    ask_col: str = "ask",
    market_mid_col: str = "market_mid",
    model_price_col: str = "model_price",
    vol_col: str = "iv_ref",
    rate_col: str = "rate_used",
    dividend_col: str = "dividend_used",
    gamma_col: str = "gamma",
    delta_col: str = "delta",
    success_col: str = "success",
) -> pl.DataFrame:
    if frame.is_empty():
        return pl.DataFrame(schema={**{k: pl.Float64 for k in POINT_DIAGNOSTIC_COLUMNS}, "within_bid_ask": pl.Boolean, "is_negative_gamma": pl.Boolean, "delta_smoothness_violation": pl.Boolean, "calendar_violation": pl.Boolean})

    base_rows = frame.to_dicts()
    diagnostics: list[dict[str, Any]] = [
        {
            "american_model_price": float("nan"),
            "model_implied_vol": float("nan"),
            "price_error_abs": float("nan"),
            "price_error_rel": float("nan"),
            "vol_error_abs": float("nan"),
            "vol_error_rel": float("nan"),
            "within_bid_ask": False,
            "bid_ask_width": float("nan"),
            "log_moneyness": float("nan"),
            "atm_distance": float("nan"),
            "is_negative_gamma": False,
            "delta_smoothness_violation": False,
            "calendar_total_variance": float("nan"),
            "calendar_violation": False,
        }
        for _ in base_rows
    ]

    for idx, row in enumerate(base_rows):
        strike = _to_float(row.get(strike_col))
        spot = _to_float(row.get(underlying_col))
        market_mid = _to_float(_candidate_value(row, (market_mid_col, "market_mid")))
        model_price = _to_float(_candidate_value(row, (model_price_col, "price", "display_price")))
        diagnostics[idx]["american_model_price"] = model_price
        bid = _to_float(row.get(bid_col))
        ask = _to_float(row.get(ask_col))
        vol = _to_float(_candidate_value(row, (vol_col, "implied_vol_input", "iv_ref", "implied_vol", "implied_vol_vendor")))
        tau = _to_float(row.get(tau_col))
        rate = _to_float(_candidate_value(row, (rate_col, "rate")))
        dividend = _to_float(_candidate_value(row, (dividend_col, "dividend")))
        is_call = str(row.get(option_type_col, "")).lower() == "call"

        if strike > 0.0 and spot > 0.0:
            log_moneyness = math.log(strike / spot)
            diagnostics[idx]["log_moneyness"] = log_moneyness
            diagnostics[idx]["atm_distance"] = abs(log_moneyness)

        if _is_finite(bid) and _is_finite(ask):
            diagnostics[idx]["bid_ask_width"] = ask - bid
            if _is_finite(model_price):
                diagnostics[idx]["within_bid_ask"] = bid - 1e-9 <= model_price <= ask + 1e-9
        elif _is_finite(model_price) and _is_finite(market_mid):
            diagnostics[idx]["within_bid_ask"] = abs(model_price - market_mid) <= max(abs(market_mid) * 0.05, 1e-9)

        if _is_finite(model_price) and _is_finite(market_mid):
            diff = model_price - market_mid
            diagnostics[idx]["price_error_abs"] = abs(diff)
            diagnostics[idx]["price_error_rel"] = abs(diff) / max(abs(market_mid), _EPS)

        if all(_is_finite(v) for v in (spot, strike, tau, rate, dividend, model_price)):
            iv = _bs_implied_vol(
                spot=spot,
                strike=strike,
                tau=tau,
                rate=rate,
                dividend=dividend,
                is_call=is_call,
                target_price=model_price,
            )
            diagnostics[idx]["model_implied_vol"] = iv
            if _is_finite(iv) and _is_finite(vol):
                diagnostics[idx]["vol_error_abs"] = abs(iv - vol)
                diagnostics[idx]["vol_error_rel"] = abs(iv - vol) / max(abs(vol), _EPS)

        gamma = _to_float(row.get(gamma_col))
        if _is_finite(gamma):
            diagnostics[idx]["is_negative_gamma"] = gamma < 0.0

    # Row-level delta smoothness within each expiry/option-type slice.
    for _, rows in _group_totals(frame, [expiry_col, option_type_col]).items():
        sorted_rows = sorted(rows, key=lambda item: _sort_key(item[1].get(strike_col)))
        if len(sorted_rows) < 2:
            continue
        expected_nonincreasing = str(sorted_rows[0][1].get(option_type_col, "")).lower() == "call"
        prev_delta = _to_float(sorted_rows[0][1].get(delta_col))
        for idx, row in sorted_rows[1:]:
            current_delta = _to_float(row.get(delta_col))
            if not (_is_finite(prev_delta) and _is_finite(current_delta)):
                prev_delta = current_delta
                continue
            if expected_nonincreasing and current_delta > prev_delta + 1e-9:
                diagnostics[idx]["delta_smoothness_violation"] = True
            if not expected_nonincreasing and current_delta < prev_delta - 1e-9:
                diagnostics[idx]["delta_smoothness_violation"] = True
            prev_delta = current_delta

    # Calendar monotonicity on total variance across expiries for each strike.
    for _, rows in _group_totals(frame, [strike_col, option_type_col]).items():
        ordered = sorted(
            rows,
            key=lambda item: _sort_key(item[1].get(tau_col)) if _is_finite(item[1].get(tau_col)) else _sort_key(item[1].get(expiry_col)),
        )
        if len(ordered) < 2:
            continue
        prev_w = float("nan")
        for idx, row in ordered:
            vol = _to_float(diagnostics[idx]["model_implied_vol"])
            if not _is_finite(vol):
                vol = _to_float(_candidate_value(row, (vol_col, "implied_vol")))
            tau = _to_float(row.get(tau_col))
            if not (_is_finite(vol) and _is_finite(tau) and tau > 0.0):
                prev_w = float("nan")
                continue
            w = vol * vol * tau
            diagnostics[idx]["calendar_total_variance"] = w
            if _is_finite(prev_w) and w + 1e-9 < prev_w:
                diagnostics[idx]["calendar_violation"] = True
            prev_w = w

    rows_out: list[dict[str, Any]] = []
    for row, diag in zip(base_rows, diagnostics, strict=False):
        merged = dict(row)
        merged.update(diag)
        rows_out.append(merged)
    return pl.DataFrame(rows_out)


def build_surface_batch_summary(frame: pl.DataFrame, *, point_diagnostics: pl.DataFrame | None = None) -> pl.DataFrame:
    if frame.is_empty():
        return pl.DataFrame(
            schema={
                "rows": pl.Int64,
                "groups": pl.Int64,
                "expiry_count": pl.Int64,
                "strike_count": pl.Int64,
                "failure_count": pl.Int64,
                "model_implied_vol_coverage": pl.Float64,
                "price_rmse": pl.Float64,
                "vol_rmse": pl.Float64,
                "atm_mae": pl.Float64,
                "wing_rmse": pl.Float64,
                "within_bid_ask_count": pl.Int64,
                "within_bid_ask_ratio": pl.Float64,
                "american_within_bid_ask_ratio": pl.Float64,
                "negative_gamma_count": pl.Int64,
                "negative_gamma_ratio": pl.Float64,
                "delta_smoothness_violation_count": pl.Int64,
                "delta_smoothness_violation_ratio": pl.Float64,
                "calendar_violation_count": pl.Int64,
                "calendar_violation_ratio": pl.Float64,
                "calendar_groups_checked": pl.Int64,
                "one_sided_drop_count": pl.Int64,
                "duplicate_conflict_count": pl.Int64,
                "strip_shape_fail_count": pl.Int64,
                "core_eligible_rows": pl.Int64,
                "density_negative_count": pl.Int64,
            }
        )

    points = point_diagnostics if point_diagnostics is not None else build_surface_point_diagnostics(frame)
    rows = points.height
    groups = frame.select(["expiration", "strike"]).unique().height if {"expiration", "strike"}.issubset(frame.columns) else rows
    expiry_count = frame.select("expiration").unique().height if "expiration" in frame.columns else 0
    strike_count = frame.select("strike").unique().height if "strike" in frame.columns else 0
    failure_count = frame.filter(~pl.col("success")).height if "success" in frame.columns else 0

    def _finite_series(col: str) -> pl.Series:
        if col not in points.columns:
            return pl.Series(name=col, values=[], dtype=pl.Float64)
        return points.get_column(col).cast(pl.Float64, strict=False)

    model_iv = _finite_series("model_implied_vol").to_list()
    vol_err = _finite_series("vol_error_abs").to_list()
    price_err = _finite_series("price_error_abs").to_list()
    atm_mask = []
    wing_mask = []
    for row in points.select(["atm_distance"]).to_dicts():
        dist = _to_float(row.get("atm_distance"))
        atm_mask.append(_is_finite(dist) and dist <= 0.05)
        wing_mask.append(_is_finite(dist) and dist >= 0.20)

    def _rmse(values: list[float], mask: list[bool] | None = None) -> float:
        subset = [
            float(v)
            for i, v in enumerate(values)
            if _is_finite(v) and (mask is None or mask[i])
        ]
        if not subset:
            return float("nan")
        return math.sqrt(sum(v * v for v in subset) / len(subset))

    within_bid_ask_count = int(points.filter(pl.col("within_bid_ask") == True).height) if "within_bid_ask" in points.columns else 0
    negative_gamma_count = int(points.filter(pl.col("is_negative_gamma") == True).height) if "is_negative_gamma" in points.columns else 0
    delta_smoothness_violation_count = int(points.filter(pl.col("delta_smoothness_violation") == True).height) if "delta_smoothness_violation" in points.columns else 0
    calendar_violation_count = int(points.filter(pl.col("calendar_violation") == True).height) if "calendar_violation" in points.columns else 0
    calendar_groups_checked = frame.select([c for c in ("strike", "option_type") if c in frame.columns]).unique().height if {"strike", "option_type"}.issubset(frame.columns) else 0
    one_sided_drop_count = int(points.filter(pl.col("one_sided_market") == True).height) if "one_sided_market" in points.columns else 0
    duplicate_conflict_count = int(points.filter(pl.col("duplicate_conflict") == True).height) if "duplicate_conflict" in points.columns else 0
    strip_shape_fail_count = int(points.filter(pl.col("strip_shape_fail") == True).height) if "strip_shape_fail" in points.columns else 0
    core_eligible_rows = int(points.filter((pl.col("eligible") == True) & (pl.col("atm_distance") <= 0.10)).height) if {"eligible", "atm_distance"}.issubset(points.columns) else 0
    density_negative_count = int(points.filter(pl.col("price_second_derivative_ref") < -1e-6).height) if "price_second_derivative_ref" in points.columns else 0

    coverage = sum(1 for v in model_iv if _is_finite(v)) / rows if rows else float("nan")
    return pl.DataFrame(
        [
            {
                "rows": rows,
                "groups": groups,
                "expiry_count": expiry_count,
                "strike_count": strike_count,
                "failure_count": failure_count,
                "model_implied_vol_coverage": coverage,
                "price_rmse": _rmse(price_err),
                "vol_rmse": _rmse(vol_err),
                "atm_mae": _rmse(vol_err, atm_mask),
                "wing_rmse": _rmse(vol_err, wing_mask),
                "within_bid_ask_count": within_bid_ask_count,
                "within_bid_ask_ratio": within_bid_ask_count / rows if rows else float("nan"),
                "american_within_bid_ask_ratio": within_bid_ask_count / rows if rows else float("nan"),
                "negative_gamma_count": negative_gamma_count,
                "negative_gamma_ratio": negative_gamma_count / rows if rows else float("nan"),
                "delta_smoothness_violation_count": delta_smoothness_violation_count,
                "delta_smoothness_violation_ratio": delta_smoothness_violation_count / rows if rows else float("nan"),
                "calendar_violation_count": calendar_violation_count,
                "calendar_violation_ratio": calendar_violation_count / rows if rows else float("nan"),
                "calendar_groups_checked": calendar_groups_checked,
                "one_sided_drop_count": one_sided_drop_count,
                "duplicate_conflict_count": duplicate_conflict_count,
                "strip_shape_fail_count": strip_shape_fail_count,
                "core_eligible_rows": core_eligible_rows,
                "density_negative_count": density_negative_count,
            }
        ]
    )


def build_surface_diagnostics(frame: pl.DataFrame) -> SurfaceDiagnosticsBundle:
    points = build_surface_point_diagnostics(frame)
    summary = build_surface_batch_summary(frame, point_diagnostics=points)
    return SurfaceDiagnosticsBundle(points=points, summary=summary)

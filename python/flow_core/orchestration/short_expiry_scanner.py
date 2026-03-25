from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import polars as pl

FOCUS_LABEL_ORDER = ("0DTE", "1DTE", "EOW")
CONTRACT_MULTIPLIER = 100.0


FOCUS_EXPIRY_SUMMARY_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.String,
    "asof_ts": pl.Datetime(time_zone="UTC"),
    "batch_id": pl.String,
    "trading_date": pl.String,
    "snapshot_kind": pl.String,
    "source_mode": pl.String,
    "focus_label": pl.String,
    "focus_order": pl.Int64,
    "expiration": pl.Date,
    "days_to_expiry": pl.Int64,
    "row_count": pl.Int64,
    "eligible_rows": pl.Int64,
    "eligible_ratio": pl.Float64,
    "within_bid_ask_ratio": pl.Float64,
    "one_sided_ratio": pl.Float64,
    "strip_shape_fail_ratio": pl.Float64,
    "atm_iv_ref": pl.Float64,
    "atm_market_mid": pl.Float64,
    "iv_skew_wing_diff": pl.Float64,
    "volume_sum": pl.Int64,
    "open_interest_sum": pl.Int64,
    "trust_score": pl.Float64,
    "trust_status": pl.String,
    "snapshot_age_sec": pl.Float64,
}

DEALER_EXPOSURE_POINTS_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.String,
    "asof_ts": pl.Datetime(time_zone="UTC"),
    "batch_id": pl.String,
    "trading_date": pl.String,
    "snapshot_kind": pl.String,
    "source_mode": pl.String,
    "focus_label": pl.String,
    "focus_order": pl.Int64,
    "expiration": pl.Date,
    "days_to_expiry": pl.Int64,
    "option_type": pl.String,
    "strike": pl.Float64,
    "underlying_price": pl.Float64,
    "volume": pl.Int64,
    "open_interest": pl.Int64,
    "eligible_ratio": pl.Float64,
    "within_bid_ask_ratio": pl.Float64,
    "avg_market_mid": pl.Float64,
    "avg_iv_ref": pl.Float64,
    "delta_exposure_oi": pl.Float64,
    "gamma_exposure_oi": pl.Float64,
    "vega_exposure_oi": pl.Float64,
    "delta_exposure_volume_proxy": pl.Float64,
    "gamma_exposure_volume_proxy": pl.Float64,
    "vega_exposure_volume_proxy": pl.Float64,
}

FLOW_PROXY_POINTS_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.String,
    "asof_ts": pl.Datetime(time_zone="UTC"),
    "batch_id": pl.String,
    "trading_date": pl.String,
    "snapshot_kind": pl.String,
    "source_mode": pl.String,
    "focus_label": pl.String,
    "focus_order": pl.Int64,
    "expiration": pl.Date,
    "days_to_expiry": pl.Int64,
    "option_type": pl.String,
    "strike": pl.Float64,
    "volume": pl.Int64,
    "open_interest": pl.Int64,
    "delta_volume": pl.Int64,
    "delta_open_interest": pl.Int64,
    "delta_avg_market_mid": pl.Float64,
    "delta_avg_iv_ref": pl.Float64,
    "delta_delta_exposure_oi": pl.Float64,
    "delta_gamma_exposure_oi": pl.Float64,
    "delta_vega_exposure_oi": pl.Float64,
    "proxy_confidence": pl.Float64,
    "proxy_reason": pl.String,
}

SCANNER_LEVELS_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.String,
    "asof_ts": pl.Datetime(time_zone="UTC"),
    "batch_id": pl.String,
    "trading_date": pl.String,
    "snapshot_kind": pl.String,
    "source_mode": pl.String,
    "focus_label": pl.String,
    "focus_order": pl.Int64,
    "expiration": pl.Date,
    "days_to_expiry": pl.Int64,
    "strike": pl.Float64,
    "total_volume": pl.Int64,
    "total_open_interest": pl.Int64,
    "call_volume": pl.Int64,
    "put_volume": pl.Int64,
    "call_open_interest": pl.Int64,
    "put_open_interest": pl.Int64,
    "avg_market_mid": pl.Float64,
    "avg_iv_ref": pl.Float64,
    "eligible_ratio": pl.Float64,
    "within_bid_ask_ratio": pl.Float64,
    "one_sided_ratio": pl.Float64,
    "strip_shape_fail_ratio": pl.Float64,
    "net_delta_exposure_oi": pl.Float64,
    "net_gamma_exposure_oi": pl.Float64,
    "net_vega_exposure_oi": pl.Float64,
    "abs_gamma_exposure_oi": pl.Float64,
    "hotspot_score": pl.Float64,
}


@dataclass(slots=True)
class ShortExpiryScannerBundle:
    focus_expiry_summary: pl.DataFrame
    dealer_exposure_points: pl.DataFrame
    flow_proxy_points: pl.DataFrame
    scanner_levels: pl.DataFrame


def empty_focus_expiry_summary() -> pl.DataFrame:
    return pl.DataFrame(schema=FOCUS_EXPIRY_SUMMARY_SCHEMA)


def empty_dealer_exposure_points() -> pl.DataFrame:
    return pl.DataFrame(schema=DEALER_EXPOSURE_POINTS_SCHEMA)


def empty_flow_proxy_points() -> pl.DataFrame:
    return pl.DataFrame(schema=FLOW_PROXY_POINTS_SCHEMA)


def empty_scanner_levels() -> pl.DataFrame:
    return pl.DataFrame(schema=SCANNER_LEVELS_SCHEMA)


def _empty_bundle() -> ShortExpiryScannerBundle:
    return ShortExpiryScannerBundle(
        focus_expiry_summary=empty_focus_expiry_summary(),
        dealer_exposure_points=empty_dealer_exposure_points(),
        flow_proxy_points=empty_flow_proxy_points(),
        scanner_levels=empty_scanner_levels(),
    )


def _to_float(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
    except Exception:
        return default
    return out if math.isfinite(out) else default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_bool(value: Any) -> bool:
    return bool(value) if value is not None else False


def _mean(values: list[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        return float("nan")
    return float(sum(finite) / len(finite))


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0.0:
        return 0.0
    return float(numerator / denominator)


def _to_utc_datetime(value: datetime | date | None) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, 16, 0, 0, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return date.fromisoformat(text[:10])
        except Exception:
            return None


def _expiry_key(value: Any) -> str:
    out = _to_date(value)
    return out.isoformat() if out is not None else ""


def _latest_batch_frame(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty():
        return frame
    out = frame
    if "asof_ts" in out.columns:
        latest_ts = out["asof_ts"].max()
        out = out.filter(pl.col("asof_ts") == latest_ts)
    if "batch_id" in out.columns and not out.is_empty():
        latest_batch = str(out["batch_id"][-1])
        out = out.filter(pl.col("batch_id").cast(pl.String) == latest_batch)
    return out


def resolve_focus_targets(
    expiries: list[str],
    *,
    asof_date: date,
    focus_labels: list[str] | None = None,
) -> list[dict[str, object]]:
    if not expiries:
        return []
    labels = [str(label).strip().upper() for label in (focus_labels or list(FOCUS_LABEL_ORDER)) if str(label).strip()]
    unique_expiries = sorted({_to_date(exp) for exp in expiries if _to_date(exp) is not None})
    if not unique_expiries:
        return []

    label_order = {label: idx for idx, label in enumerate(FOCUS_LABEL_ORDER)}
    next_friday = asof_date + timedelta(days=(4 - asof_date.weekday()) % 7)
    targets: list[dict[str, object]] = []

    for label in labels:
        chosen: date | None = None
        if label == "0DTE":
            chosen = next((exp for exp in unique_expiries if exp == asof_date), None)
        elif label == "1DTE":
            target_date = asof_date + timedelta(days=1)
            chosen = next((exp for exp in unique_expiries if exp == target_date), None)
            if chosen is None:
                chosen = next((exp for exp in unique_expiries if exp > asof_date), None)
        elif label == "EOW":
            same_week = [exp for exp in unique_expiries if asof_date <= exp <= next_friday]
            chosen = max(same_week) if same_week else next((exp for exp in unique_expiries if exp >= asof_date), None)
        else:
            chosen = next((exp for exp in unique_expiries if exp >= asof_date), None)
        if chosen is None:
            continue
        targets.append(
            {
                "focus_label": label,
                "focus_order": int(label_order.get(label, len(label_order))),
                "expiration": chosen,
                "days_to_expiry": max((chosen - asof_date).days, 0),
            }
        )
    return targets


def resolve_focus_expirations(
    expiries: list[str],
    *,
    asof_date: date,
    focus_labels: list[str] | None = None,
) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for target in resolve_focus_targets(expiries, asof_date=asof_date, focus_labels=focus_labels):
        exp = str(target["expiration"])
        if exp in seen:
            continue
        seen.add(exp)
        ordered.append(exp)
    return ordered


def _annotate_focus_rows(rows: list[dict[str, Any]], targets: list[dict[str, object]]) -> list[dict[str, Any]]:
    exp_to_targets: dict[str, list[dict[str, object]]] = defaultdict(list)
    for target in targets:
        exp_to_targets[_expiry_key(target.get("expiration"))].append(target)

    out: list[dict[str, Any]] = []
    for row in rows:
        expiry_targets = exp_to_targets.get(_expiry_key(row.get("expiration")), [])
        for target in expiry_targets:
            enriched = dict(row)
            enriched["focus_label"] = str(target["focus_label"])
            enriched["focus_order"] = int(target["focus_order"])
            enriched["focus_expiration"] = target["expiration"]
            enriched["focus_days_to_expiry"] = int(target["days_to_expiry"])
            out.append(enriched)
    return out


def _scanner_base_rows(raw: pl.DataFrame, surface_points: pl.DataFrame) -> list[dict[str, Any]]:
    latest_raw = _latest_batch_frame(raw)
    latest_surface = _latest_batch_frame(surface_points)
    if latest_surface.is_empty():
        return []

    join_keys = [key for key in ("symbol", "contract_symbol", "option_type", "strike") if key in latest_surface.columns and key in latest_raw.columns]
    raw_cols = [key for key in (*join_keys, "volume", "open_interest") if key in latest_raw.columns]
    merged = latest_surface
    if join_keys and raw_cols:
        merged = latest_surface.join(latest_raw.select(raw_cols), on=join_keys, how="left", suffix="_raw")
    return merged.to_dicts()


def _dealer_input_rows(raw: pl.DataFrame, greeks: pl.DataFrame, surface_points: pl.DataFrame) -> list[dict[str, Any]]:
    latest_raw = _latest_batch_frame(raw)
    latest_greeks = _latest_batch_frame(greeks)
    latest_surface = _latest_batch_frame(surface_points)
    if latest_greeks.is_empty():
        return []

    join_keys = [key for key in ("symbol", "contract_symbol", "option_type", "strike") if key in latest_greeks.columns and key in latest_raw.columns]
    raw_cols = [key for key in (*join_keys, "volume", "open_interest") if key in latest_raw.columns]
    surface_cols = [
        key
        for key in (
            *join_keys,
            "eligible",
            "within_bid_ask",
            "iv_ref",
            "market_mid",
        )
        if key in latest_surface.columns
    ]
    merged = latest_greeks
    if join_keys and raw_cols:
        merged = merged.join(latest_raw.select(raw_cols), on=join_keys, how="left", suffix="_raw")
    if join_keys and surface_cols:
        merged = merged.join(latest_surface.select(surface_cols), on=join_keys, how="left", suffix="_surface")
    return merged.to_dicts()


def _build_focus_expiry_summary(
    rows: list[dict[str, Any]],
    *,
    asof_ts: datetime,
    batch_id: str,
    symbol: str,
    trading_date: str,
    snapshot_kind: str,
    source_mode: str,
) -> pl.DataFrame:
    if not rows:
        return empty_focus_expiry_summary()

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row["focus_label"]), _expiry_key(row["focus_expiration"]))
        grouped[key].append(row)

    now_utc = datetime.now(timezone.utc)
    summary_rows: list[dict[str, Any]] = []
    for (_, _), bucket in sorted(grouped.items(), key=lambda item: (int(item[1][0].get("focus_order", 0)), item[0][1])):
        first = bucket[0]
        row_count = len(bucket)
        eligible_rows = sum(1 for row in bucket if _to_bool(row.get("eligible")))
        within_rows = sum(1 for row in bucket if _to_bool(row.get("within_bid_ask")))
        one_sided = sum(1 for row in bucket if _to_bool(row.get("one_sided_market")))
        strip_fails = sum(1 for row in bucket if _to_bool(row.get("strip_shape_fail")))
        atm_rows = sorted(bucket, key=lambda row: abs(_to_float(row.get("atm_distance"), 1e9)))[:4]
        skew_sorted = sorted(
            [row for row in bucket if math.isfinite(_to_float(row.get("iv_ref")))],
            key=lambda row: _to_float(row.get("strike")),
        )
        iv_skew = float("nan")
        if len(skew_sorted) >= 2:
            iv_skew = _to_float(skew_sorted[-1].get("iv_ref")) - _to_float(skew_sorted[0].get("iv_ref"))
        eligible_ratio = _safe_ratio(float(eligible_rows), float(row_count))
        within_ratio = _safe_ratio(float(within_rows), float(row_count))
        one_sided_ratio = _safe_ratio(float(one_sided), float(row_count))
        strip_ratio = _safe_ratio(float(strip_fails), float(row_count))
        trust_score = 100.0 * (
            0.40 * eligible_ratio
            + 0.30 * within_ratio
            + 0.15 * (1.0 - strip_ratio)
            + 0.15 * (1.0 - one_sided_ratio)
        )
        trust_status = "trusted" if trust_score >= 80.0 else "review" if trust_score >= 60.0 else "caution"
        summary_rows.append(
            {
                "symbol": symbol,
                "asof_ts": asof_ts,
                "batch_id": batch_id,
                "trading_date": trading_date,
                "snapshot_kind": snapshot_kind,
                "source_mode": source_mode,
                "focus_label": str(first["focus_label"]),
                "focus_order": int(first["focus_order"]),
                "expiration": _to_date(first["focus_expiration"]),
                "days_to_expiry": int(first["focus_days_to_expiry"]),
                "row_count": row_count,
                "eligible_rows": eligible_rows,
                "eligible_ratio": eligible_ratio,
                "within_bid_ask_ratio": within_ratio,
                "one_sided_ratio": one_sided_ratio,
                "strip_shape_fail_ratio": strip_ratio,
                "atm_iv_ref": _mean([_to_float(row.get("iv_ref")) for row in atm_rows]),
                "atm_market_mid": _mean([_to_float(row.get("market_mid")) for row in atm_rows]),
                "iv_skew_wing_diff": iv_skew,
                "volume_sum": sum(_to_int(row.get("volume")) for row in bucket),
                "open_interest_sum": sum(_to_int(row.get("open_interest")) for row in bucket),
                "trust_score": trust_score,
                "trust_status": trust_status,
                "snapshot_age_sec": max((now_utc - asof_ts).total_seconds(), 0.0),
            }
        )
    return pl.DataFrame(summary_rows, schema=FOCUS_EXPIRY_SUMMARY_SCHEMA).sort(["focus_order", "expiration"])


def _build_dealer_exposure_points(
    rows: list[dict[str, Any]],
    *,
    asof_ts: datetime,
    batch_id: str,
    symbol: str,
    trading_date: str,
    snapshot_kind: str,
    source_mode: str,
) -> pl.DataFrame:
    if not rows:
        return empty_dealer_exposure_points()

    grouped: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row["focus_label"]),
            int(row["focus_order"]),
            _to_date(row["focus_expiration"]),
            int(row["focus_days_to_expiry"]),
            str(row.get("option_type", "")),
            _to_float(row.get("strike")),
        )
        grouped[key].append(row)

    out_rows: list[dict[str, Any]] = []
    for key, bucket in grouped.items():
        focus_label, focus_order, expiration, days_to_expiry, option_type, strike = key
        spot = _mean([_to_float(row.get("underlying_price")) for row in bucket])
        volume = sum(_to_int(row.get("volume")) for row in bucket)
        open_interest = sum(_to_int(row.get("open_interest")) for row in bucket)
        delta_exposure_oi = sum(
            _to_float(row.get("delta")) * _to_int(row.get("open_interest")) * CONTRACT_MULTIPLIER * _to_float(row.get("underlying_price"), 0.0)
            for row in bucket
        )
        gamma_exposure_oi = sum(
            _to_float(row.get("gamma")) * _to_int(row.get("open_interest")) * CONTRACT_MULTIPLIER * (_to_float(row.get("underlying_price"), 0.0) ** 2)
            for row in bucket
        )
        vega_exposure_oi = sum(
            _to_float(row.get("vega")) * _to_int(row.get("open_interest")) * CONTRACT_MULTIPLIER
            for row in bucket
        )
        delta_exposure_volume = sum(
            _to_float(row.get("delta")) * _to_int(row.get("volume")) * CONTRACT_MULTIPLIER * _to_float(row.get("underlying_price"), 0.0)
            for row in bucket
        )
        gamma_exposure_volume = sum(
            _to_float(row.get("gamma")) * _to_int(row.get("volume")) * CONTRACT_MULTIPLIER * (_to_float(row.get("underlying_price"), 0.0) ** 2)
            for row in bucket
        )
        vega_exposure_volume = sum(
            _to_float(row.get("vega")) * _to_int(row.get("volume")) * CONTRACT_MULTIPLIER
            for row in bucket
        )
        out_rows.append(
            {
                "symbol": symbol,
                "asof_ts": asof_ts,
                "batch_id": batch_id,
                "trading_date": trading_date,
                "snapshot_kind": snapshot_kind,
                "source_mode": source_mode,
                "focus_label": focus_label,
                "focus_order": focus_order,
                "expiration": expiration,
                "days_to_expiry": days_to_expiry,
                "option_type": option_type,
                "strike": strike,
                "underlying_price": spot,
                "volume": volume,
                "open_interest": open_interest,
                "eligible_ratio": _safe_ratio(sum(1 for row in bucket if _to_bool(row.get("eligible"))), len(bucket)),
                "within_bid_ask_ratio": _safe_ratio(sum(1 for row in bucket if _to_bool(row.get("within_bid_ask"))), len(bucket)),
                "avg_market_mid": _mean([_to_float(row.get("market_mid")) for row in bucket]),
                "avg_iv_ref": _mean([_to_float(row.get("iv_ref")) for row in bucket]),
                "delta_exposure_oi": delta_exposure_oi,
                "gamma_exposure_oi": gamma_exposure_oi,
                "vega_exposure_oi": vega_exposure_oi,
                "delta_exposure_volume_proxy": delta_exposure_volume,
                "gamma_exposure_volume_proxy": gamma_exposure_volume,
                "vega_exposure_volume_proxy": vega_exposure_volume,
            }
        )
    return pl.DataFrame(out_rows, schema=DEALER_EXPOSURE_POINTS_SCHEMA).sort(
        ["focus_order", "expiration", "option_type", "strike"]
    )


def _build_flow_proxy_points(
    current: pl.DataFrame,
    previous_history: pl.DataFrame,
    *,
    asof_ts: datetime,
    batch_id: str,
    symbol: str,
    trading_date: str,
    snapshot_kind: str,
    source_mode: str,
) -> pl.DataFrame:
    if current.is_empty():
        return empty_flow_proxy_points()

    previous = _latest_batch_frame(previous_history)
    keys = ["focus_label", "expiration", "option_type", "strike"]
    previous_lookup = {
        (
            str(row.get("focus_label", "")),
            _to_date(row.get("expiration")),
            str(row.get("option_type", "")),
            _to_float(row.get("strike")),
        ): row
        for row in previous.to_dicts()
    }

    out_rows: list[dict[str, Any]] = []
    for row in current.to_dicts():
        key = (
            str(row.get("focus_label", "")),
            _to_date(row.get("expiration")),
            str(row.get("option_type", "")),
            _to_float(row.get("strike")),
        )
        prev = previous_lookup.get(key)
        has_prev = prev is not None
        delta_volume = _to_int(row.get("volume")) - (_to_int(prev.get("volume")) if prev else 0)
        delta_oi = _to_int(row.get("open_interest")) - (_to_int(prev.get("open_interest")) if prev else 0)
        delta_mid = _to_float(row.get("avg_market_mid")) - (_to_float(prev.get("avg_market_mid")) if prev else 0.0)
        delta_iv = _to_float(row.get("avg_iv_ref")) - (_to_float(prev.get("avg_iv_ref")) if prev else 0.0)
        delta_delta = _to_float(row.get("delta_exposure_oi")) - (_to_float(prev.get("delta_exposure_oi")) if prev else 0.0)
        delta_gamma = _to_float(row.get("gamma_exposure_oi")) - (_to_float(prev.get("gamma_exposure_oi")) if prev else 0.0)
        delta_vega = _to_float(row.get("vega_exposure_oi")) - (_to_float(prev.get("vega_exposure_oi")) if prev else 0.0)
        confidence = 0.15
        if has_prev:
            confidence += 0.20
        if abs(delta_volume) > 0:
            confidence += 0.25
        if abs(delta_oi) > 0:
            confidence += 0.20
        if _to_float(row.get("eligible_ratio"), 0.0) >= 0.70:
            confidence += 0.10
        if _to_float(row.get("within_bid_ask_ratio"), 0.0) >= 0.70:
            confidence += 0.10
        out_rows.append(
            {
                "symbol": symbol,
                "asof_ts": asof_ts,
                "batch_id": batch_id,
                "trading_date": trading_date,
                "snapshot_kind": snapshot_kind,
                "source_mode": source_mode,
                "focus_label": str(row.get("focus_label", "")),
                "focus_order": int(row.get("focus_order", 0)),
                "expiration": _to_date(row.get("expiration")),
                "days_to_expiry": int(row.get("days_to_expiry", 0)),
                "option_type": str(row.get("option_type", "")),
                "strike": _to_float(row.get("strike")),
                "volume": _to_int(row.get("volume")),
                "open_interest": _to_int(row.get("open_interest")),
                "delta_volume": delta_volume,
                "delta_open_interest": delta_oi,
                "delta_avg_market_mid": delta_mid,
                "delta_avg_iv_ref": delta_iv,
                "delta_delta_exposure_oi": delta_delta,
                "delta_gamma_exposure_oi": delta_gamma,
                "delta_vega_exposure_oi": delta_vega,
                "proxy_confidence": min(confidence, 0.95),
                "proxy_reason": "snapshot_delta_proxy_not_trade_tape" if has_prev else "no_previous_scanner_batch",
            }
        )
    return pl.DataFrame(out_rows, schema=FLOW_PROXY_POINTS_SCHEMA).sort(["focus_order", "expiration", "option_type", "strike"])


def _build_scanner_levels(
    base_rows: list[dict[str, Any]],
    dealer_points: pl.DataFrame,
    *,
    asof_ts: datetime,
    batch_id: str,
    symbol: str,
    trading_date: str,
    snapshot_kind: str,
    source_mode: str,
) -> pl.DataFrame:
    if not base_rows:
        return empty_scanner_levels()

    grouped: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in base_rows:
        key = (
            str(row["focus_label"]),
            int(row["focus_order"]),
            _to_date(row["focus_expiration"]),
            int(row["focus_days_to_expiry"]),
            _to_float(row.get("strike")),
        )
        grouped[key].append(row)

    dealer_lookup: dict[tuple[object, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in dealer_points.to_dicts():
        dealer_key = (
            str(row.get("focus_label", "")),
            int(row.get("focus_order", 0)),
            _to_date(row.get("expiration")),
            int(row.get("days_to_expiry", 0)),
            _to_float(row.get("strike")),
        )
        dealer_lookup[dealer_key].append(row)

    out_rows: list[dict[str, Any]] = []
    for key, bucket in grouped.items():
        focus_label, focus_order, expiration, days_to_expiry, strike = key
        dealers = dealer_lookup.get(key, [])
        call_rows = [row for row in bucket if str(row.get("option_type", "")).lower() == "call"]
        put_rows = [row for row in bucket if str(row.get("option_type", "")).lower() == "put"]
        total_volume = sum(_to_int(row.get("volume")) for row in bucket)
        total_oi = sum(_to_int(row.get("open_interest")) for row in bucket)
        net_delta_oi = sum(_to_float(row.get("delta_exposure_oi")) for row in dealers)
        net_gamma_oi = sum(_to_float(row.get("gamma_exposure_oi")) for row in dealers)
        net_vega_oi = sum(_to_float(row.get("vega_exposure_oi")) for row in dealers)
        abs_gamma_oi = abs(net_gamma_oi)
        hotspot_score = math.log1p(abs_gamma_oi) + 0.40 * math.log1p(max(total_oi, 0)) + 0.20 * math.log1p(max(total_volume, 0))
        out_rows.append(
            {
                "symbol": symbol,
                "asof_ts": asof_ts,
                "batch_id": batch_id,
                "trading_date": trading_date,
                "snapshot_kind": snapshot_kind,
                "source_mode": source_mode,
                "focus_label": focus_label,
                "focus_order": focus_order,
                "expiration": expiration,
                "days_to_expiry": days_to_expiry,
                "strike": strike,
                "total_volume": total_volume,
                "total_open_interest": total_oi,
                "call_volume": sum(_to_int(row.get("volume")) for row in call_rows),
                "put_volume": sum(_to_int(row.get("volume")) for row in put_rows),
                "call_open_interest": sum(_to_int(row.get("open_interest")) for row in call_rows),
                "put_open_interest": sum(_to_int(row.get("open_interest")) for row in put_rows),
                "avg_market_mid": _mean([_to_float(row.get("market_mid")) for row in bucket]),
                "avg_iv_ref": _mean([_to_float(row.get("iv_ref")) for row in bucket]),
                "eligible_ratio": _safe_ratio(sum(1 for row in bucket if _to_bool(row.get("eligible"))), len(bucket)),
                "within_bid_ask_ratio": _safe_ratio(sum(1 for row in bucket if _to_bool(row.get("within_bid_ask"))), len(bucket)),
                "one_sided_ratio": _safe_ratio(sum(1 for row in bucket if _to_bool(row.get("one_sided_market"))), len(bucket)),
                "strip_shape_fail_ratio": _safe_ratio(sum(1 for row in bucket if _to_bool(row.get("strip_shape_fail"))), len(bucket)),
                "net_delta_exposure_oi": net_delta_oi,
                "net_gamma_exposure_oi": net_gamma_oi,
                "net_vega_exposure_oi": net_vega_oi,
                "abs_gamma_exposure_oi": abs_gamma_oi,
                "hotspot_score": hotspot_score,
            }
        )
    return pl.DataFrame(out_rows, schema=SCANNER_LEVELS_SCHEMA).sort(["focus_order", "expiration", "hotspot_score"], descending=[False, False, True])


def build_short_expiry_scanner_bundle(
    *,
    raw: pl.DataFrame,
    greeks: pl.DataFrame,
    surface_points: pl.DataFrame,
    previous_dealer_exposure_points: pl.DataFrame | None = None,
    focus_labels: list[str] | None = None,
    symbol: str,
    asof_ts: datetime,
    batch_id: str,
    trading_date: str,
    snapshot_kind: str,
    source_mode: str,
) -> ShortExpiryScannerBundle:
    if raw.is_empty() and greeks.is_empty() and surface_points.is_empty():
        return _empty_bundle()

    latest_surface = _latest_batch_frame(surface_points)
    latest_expiries = [
        exp
        for exp in {_expiry_key(value) for value in latest_surface.get_column("expiration").to_list()} if exp
    ] if not latest_surface.is_empty() and "expiration" in latest_surface.columns else []
    if not latest_expiries and "expiration" in raw.columns:
        latest_expiries = [
            exp
            for exp in {_expiry_key(value) for value in _latest_batch_frame(raw).get_column("expiration").to_list()} if exp
        ]
    targets = resolve_focus_targets(latest_expiries, asof_date=_to_utc_datetime(asof_ts).date(), focus_labels=focus_labels)
    if not targets:
        return _empty_bundle()

    base_rows = _annotate_focus_rows(_scanner_base_rows(raw, surface_points), targets)
    dealer_rows = _annotate_focus_rows(_dealer_input_rows(raw, greeks, surface_points), targets)

    focus_expiry_summary = _build_focus_expiry_summary(
        base_rows,
        asof_ts=asof_ts,
        batch_id=batch_id,
        symbol=symbol,
        trading_date=trading_date,
        snapshot_kind=snapshot_kind,
        source_mode=source_mode,
    )
    dealer_exposure_points = _build_dealer_exposure_points(
        dealer_rows,
        asof_ts=asof_ts,
        batch_id=batch_id,
        symbol=symbol,
        trading_date=trading_date,
        snapshot_kind=snapshot_kind,
        source_mode=source_mode,
    )
    flow_proxy_points = _build_flow_proxy_points(
        dealer_exposure_points,
        previous_dealer_exposure_points if previous_dealer_exposure_points is not None else pl.DataFrame(),
        asof_ts=asof_ts,
        batch_id=batch_id,
        symbol=symbol,
        trading_date=trading_date,
        snapshot_kind=snapshot_kind,
        source_mode=source_mode,
    )
    scanner_levels = _build_scanner_levels(
        base_rows,
        dealer_exposure_points,
        asof_ts=asof_ts,
        batch_id=batch_id,
        symbol=symbol,
        trading_date=trading_date,
        snapshot_kind=snapshot_kind,
        source_mode=source_mode,
    )
    return ShortExpiryScannerBundle(
        focus_expiry_summary=focus_expiry_summary,
        dealer_exposure_points=dealer_exposure_points,
        flow_proxy_points=flow_proxy_points,
        scanner_levels=scanner_levels,
    )

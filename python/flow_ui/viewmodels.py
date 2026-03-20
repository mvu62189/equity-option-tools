from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from flow_core.orchestration.state_store import SymbolSnapshot


def _empty_overlay(reason: str = "no_data") -> dict[str, Any]:
    heat = np.ascontiguousarray(np.zeros((1, 1), dtype=np.float32))
    return {
        "line_series": {},
        "heat_image": heat,
        "rect": (0.0, 0.0, 1.0, 1.0),
        "levels": (0.0, 1.0),
        "meta": {
            "status": reason,
            "rows": 0,
            "space_mode": "strike",
            "payload_bytes": int(heat.nbytes),
            "y_axis_mode": "days_to_expiry",
            "y_axis_values": [0.0],
            "y_axis_labels": ["n/a"],
            "is_single_expiry": True,
            "expiries_loaded": [],
            "chart_explanation": "No routed-Greeks data is available for the requested overlay.",
            "data_source": "routed_greeks (empty)",
        },
    }


def _fill_sparse(mat: np.ndarray) -> np.ndarray:
    out = mat.copy()
    rows, cols = out.shape
    for i in range(rows):
        row = out[i]
        mask = np.isnan(row)
        if mask.all():
            continue
        valid_idx = np.where(~mask)[0]
        left = int(valid_idx[0])
        right = int(valid_idx[-1])
        row[:left] = row[left]
        row[right + 1 :] = row[right]
        for j in range(left + 1, right):
            if np.isnan(row[j]):
                row[j] = row[j - 1]
        out[i] = row
    col_means = np.nanmean(out, axis=0)
    col_means = np.where(np.isnan(col_means), 0.0, col_means)
    inds = np.where(np.isnan(out))
    out[inds] = col_means[inds[1]]
    return np.where(np.isfinite(out), out, 0.0)


def _robust_levels(values: np.ndarray) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return (0.0, 1.0)
    lo = float(np.quantile(finite, 0.05))
    hi = float(np.quantile(finite, 0.95))
    if not np.isfinite(lo) or not np.isfinite(hi) or lo == hi:
        center = float(np.nanmean(finite))
        span = max(float(np.nanstd(finite)), 1e-6)
        return (center - span, center + span)
    return (lo, hi)


def _grid_from_frame(frame: pl.DataFrame, value_col: str) -> tuple[np.ndarray, list[float], list[str], list[float]]:
    group_cols = ["expiration", "strike"]
    if "days_to_expiry" in frame.columns:
        group_cols = ["expiration", "days_to_expiry", "strike"]
    agg = (
        frame.group_by(group_cols)
        .agg(pl.mean(value_col).alias("value"))
        .sort([c for c in ("days_to_expiry", "expiration", "strike") if c in group_cols])
    )
    exp_vals = [str(x) for x in agg["expiration"].to_list()]
    exp_unique = sorted(set(exp_vals))
    if "days_to_expiry" in agg.columns:
        exp_to_day = {}
        for row in agg.select(["expiration", "days_to_expiry"]).unique().to_dicts():
            exp_to_day[str(row["expiration"])] = float(row["days_to_expiry"])
        y_vals = [exp_to_day.get(exp, float(idx)) for idx, exp in enumerate(exp_unique)]
    else:
        y_vals = [float(idx) for idx, _exp in enumerate(exp_unique)]
    strike_vals = sorted({float(x) for x in agg["strike"].to_list()})
    mat = np.full((len(exp_unique), len(strike_vals)), np.nan, dtype=np.float32)
    exp_index = {v: i for i, v in enumerate(exp_unique)}
    strike_index = {v: i for i, v in enumerate(strike_vals)}
    for row in agg.to_dicts():
        i = exp_index[str(row["expiration"])]
        j = strike_index[float(row["strike"])]
        mat[i, j] = float(row["value"])
    return _fill_sparse(mat).astype(np.float32, copy=False), strike_vals, exp_unique, y_vals


def _x_transform(strikes: np.ndarray, forward: float, space_mode: str) -> np.ndarray:
    if space_mode == "log":
        f = max(float(forward), 1e-8)
        return np.log(np.maximum(strikes, 1e-8) / f).astype(np.float32, copy=False)
    return strikes.astype(np.float32, copy=False)


def _snapshot_source(snapshot: SymbolSnapshot, dataset: str) -> str:
    return (
        f"{dataset} batch={snapshot.batch_id} "
        f"snapshot_kind={snapshot.snapshot_kind} source_mode={snapshot.source_mode}"
    )


def build_overlay_payload(
    snapshot: SymbolSnapshot,
    greek: str,
    option_type: str,
    expiry_filter: str,
    *,
    space_mode: str = "strike",
    engine_mask: set[str] | None = None,
    dual_mode: bool = False,
) -> dict[str, Any]:
    greeks = snapshot.greeks
    if greeks.is_empty() or greek not in greeks.columns:
        return _empty_overlay("missing_greeks")

    filt = greeks
    if option_type == "all" and "option_type" in filt.columns:
        option_types = {str(x) for x in filt["option_type"].drop_nulls().to_list()}
        if len(option_types) > 1:
            return _empty_overlay("select_call_or_put_for_overlay")
    if option_type != "all" and "option_type" in filt.columns:
        filt = filt.filter(pl.col("option_type") == option_type)
    if expiry_filter != "all" and "expiration" in filt.columns:
        filt = filt.filter(pl.col("expiration").cast(pl.String) == expiry_filter)
    if engine_mask:
        engine_col = "engine_used" if "engine_used" in filt.columns else "greeks_engine"
        if engine_col in filt.columns:
            filt = filt.filter(pl.col(engine_col).is_in(sorted(engine_mask)))
    if "expiration" not in filt.columns:
        filt = filt.with_columns(pl.lit("all").alias("expiration"))

    required = {"strike", greek}
    if not required.issubset(set(filt.columns)):
        return _empty_overlay("missing_columns")
    filt = filt.filter(pl.col("strike").is_not_null() & pl.col(greek).is_not_null() & pl.col(greek).is_finite())
    if filt.is_empty():
        return _empty_overlay("filtered_empty")

    forward = float(filt["underlying_price"][0]) if "underlying_price" in filt.columns else 1.0
    engine_col = "engine_used" if "engine_used" in filt.columns else "greeks_engine"
    line_series: dict[str, np.ndarray] = {}
    engine_values = (
        sorted({str(x) for x in filt[engine_col].drop_nulls().to_list()})
        if engine_col in filt.columns
        else []
    )
    if engine_col in filt.columns:
        for engine, sub in filt.partition_by(engine_col, as_dict=True).items():
            name = str(engine[0] if isinstance(engine, tuple) else engine)
            pts = sub.select(["strike", greek]).sort("strike")
            x = _x_transform(np.asarray(pts["strike"].to_list(), dtype=np.float32), forward=forward, space_mode=space_mode)
            y = np.asarray(pts[greek].to_list(), dtype=np.float32)
            arr = np.column_stack([x, y])
            line_series[name] = np.ascontiguousarray(arr, dtype=np.float32)

    heat_frame = filt
    heat_status = "ok"
    if len(engine_values) > 1 and engine_col in filt.columns:
        heat_frame = filt.filter(pl.col(engine_col) == engine_values[0])
        heat_status = f"heatmap_single_engine:{engine_values[0]}"

    if space_mode == "residual" and "space_mode" in heat_frame.columns:
        left = heat_frame.filter(pl.col("space_mode") == "log").group_by(["expiration", "strike"]).agg(pl.mean(greek).alias("log_val"))
        right = (
            heat_frame.filter(pl.col("space_mode") == "strike")
            .group_by(["expiration", "strike"])
            .agg(pl.mean(greek).alias("strike_val"))
        )
        joined = left.join(right, on=["expiration", "strike"], how="inner")
        if not joined.is_empty():
            filt_res = joined.with_columns((pl.col("log_val") - pl.col("strike_val")).alias("residual")).select(
                ["expiration", "strike", "residual"]
            )
            if "days_to_expiry" in heat_frame.columns:
                filt_res = filt_res.join(
                    heat_frame.select(["expiration", "days_to_expiry"]).unique(),
                    on="expiration",
                    how="left",
                )
            mat, strike_vals, exp_unique, y_vals = _grid_from_frame(
                filt_res.rename({"residual": greek}),
                greek,
            )
            residual_stats = {
                "mean": float(np.mean(mat)),
                "std": float(np.std(mat)),
                "abs_max": float(np.max(np.abs(mat))),
            }
        else:
            mat, strike_vals, exp_unique, y_vals = _grid_from_frame(heat_frame, greek)
            mat = np.zeros_like(mat, dtype=np.float32)
            residual_stats = {"mean": 0.0, "std": 0.0, "abs_max": 0.0}
    else:
        mat, strike_vals, exp_unique, y_vals = _grid_from_frame(heat_frame, greek)
        residual_stats = {}

    mat = np.ascontiguousarray(mat, dtype=np.float32)
    levels = _robust_levels(mat)
    x_vals = _x_transform(np.asarray(strike_vals, dtype=np.float32), forward=forward, space_mode=space_mode)
    x_min = float(np.min(x_vals))
    x_max = float(np.max(x_vals))
    y_min = float(min(y_vals)) if y_vals else 0.0
    y_max = float(max(y_vals)) if y_vals else 0.0
    if len(y_vals) <= 1:
        rect = (x_min, y_min - 0.5, max(x_max - x_min, 1e-6), 1.0)
    else:
        rect = (x_min, y_min, max(x_max - x_min, 1e-6), max(y_max - y_min, 1.0))

    payload: dict[str, Any] = {
        "line_series": line_series,
        "heat_image": mat,
        "rect": rect,
        "levels": levels,
        "meta": {
            "status": heat_status,
            "rows": int(filt.height),
            "space_mode": space_mode,
            "residual_stats": residual_stats,
            "payload_bytes": int(mat.nbytes + sum(v.nbytes for v in line_series.values())),
            "y_axis_mode": "days_to_expiry",
            "y_axis_values": [float(x) for x in y_vals],
            "y_axis_labels": [str(x) for x in exp_unique],
            "is_single_expiry": len(exp_unique) <= 1,
            "expiries_loaded": list(exp_unique),
            "engine_scope": engine_values,
            "option_scope": option_type,
            "heat_engine": engine_values[0] if engine_values else "",
            "chart_explanation": (
                f"Line plot: {greek}(x) for routed engines on the selected expiry scope. "
                f"Heatmap: {greek} across {space_mode} x-axis and days-to-expiry y-axis."
            ),
            "data_source": _snapshot_source(snapshot, "routed_greeks"),
        },
    }

    if dual_mode and space_mode in {"log", "strike"}:
        other_mode = "strike" if space_mode == "log" else "log"
        x_other = _x_transform(np.asarray(strike_vals, dtype=np.float32), forward=forward, space_mode=other_mode)
        payload["heat_image_secondary"] = mat.copy()
        payload["rect_secondary"] = (
            float(np.min(x_other)),
            rect[1],
            max(float(np.max(x_other) - np.min(x_other)), 1e-6),
            rect[3],
        )
    return payload


def build_price_error_payload(
    snapshot: SymbolSnapshot,
    option_type: str,
    expiry_filter: str,
    *,
    engine_mask: set[str] | None = None,
    relative: bool = False,
) -> dict[str, Any]:
    greeks = snapshot.greeks
    if greeks.is_empty():
        return {
            "line_series": {},
            "error_series": {},
            "meta": {
                "status": "missing_greeks",
                "chart_explanation": "No routed-Greeks rows are available for model-versus-market comparison.",
                "data_source": _snapshot_source(snapshot, "routed_greeks"),
            },
        }

    filt = greeks
    if option_type == "all" and "option_type" in filt.columns:
        option_types = {str(x) for x in filt["option_type"].drop_nulls().to_list()}
        if len(option_types) > 1:
            return {
                "line_series": {},
                "error_series": {},
                "meta": {
                    "status": "select_call_or_put_for_price_error",
                    "chart_explanation": "Choose call or put so model-versus-market price error is not mixed across option types.",
                    "data_source": _snapshot_source(snapshot, "routed_greeks"),
                },
            }
    if option_type != "all" and "option_type" in filt.columns:
        filt = filt.filter(pl.col("option_type") == option_type)
    if expiry_filter != "all" and "expiration" in filt.columns:
        filt = filt.filter(pl.col("expiration").cast(pl.String) == expiry_filter)
    if engine_mask and "engine_used" in filt.columns:
        filt = filt.filter(pl.col("engine_used").is_in(sorted(engine_mask)))
    required = {"strike", "model_price", "market_mid"}
    if not required.issubset(filt.columns):
        return {
            "line_series": {},
            "error_series": {},
            "meta": {
                "status": "missing_columns",
                "chart_explanation": "The routed-Greeks table is missing model or market price columns.",
                "data_source": _snapshot_source(snapshot, "routed_greeks"),
            },
        }
    filt = filt.filter(
        pl.col("strike").is_not_null()
        & pl.col("model_price").is_not_null()
        & pl.col("market_mid").is_not_null()
        & pl.col("model_price").is_finite()
        & pl.col("market_mid").is_finite()
    )
    if filt.is_empty():
        return {
            "line_series": {},
            "error_series": {},
            "meta": {
                "status": "filtered_empty",
                "chart_explanation": "No finite model-versus-market price rows survived the current filters.",
                "data_source": _snapshot_source(snapshot, "routed_greeks"),
            },
        }

    if "expiration" in filt.columns and expiry_filter == "all":
        first_expiry = str(filt["expiration"][0])
        filt = filt.filter(pl.col("expiration").cast(pl.String) == first_expiry)
    mode = "relative" if relative else "absolute"
    if relative:
        filt = filt.with_columns(
            pl.when(pl.col("market_mid").abs() > 1e-8)
            .then((pl.col("model_price") - pl.col("market_mid")) / pl.col("market_mid").abs())
            .otherwise(0.0)
            .alias("price_error")
        )
    else:
        filt = filt.with_columns((pl.col("model_price") - pl.col("market_mid")).alias("price_error"))

    if "engine_used" in filt.columns:
        grouped = filt.partition_by("engine_used", as_dict=True)
        line_series = {}
        error_series = {}
        for engine, sub in grouped.items():
            name = str(engine[0] if isinstance(engine, tuple) else engine)
            pts = sub.select(["strike", "model_price", "market_mid", "price_error"]).sort("strike")
            strikes = np.asarray(pts["strike"].to_list(), dtype=np.float32)
            model = np.asarray(pts["model_price"].to_list(), dtype=np.float32)
            market = np.asarray(pts["market_mid"].to_list(), dtype=np.float32)
            error = np.asarray(pts["price_error"].to_list(), dtype=np.float32)
            line_series[f"{name}:model"] = np.ascontiguousarray(np.column_stack([strikes, model]), dtype=np.float32)
            line_series[f"{name}:market"] = np.ascontiguousarray(np.column_stack([strikes, market]), dtype=np.float32)
            error_series[name] = np.ascontiguousarray(np.column_stack([strikes, error]), dtype=np.float32)
    else:
        pts = filt.select(["strike", "model_price", "market_mid", "price_error"]).sort("strike")
        strikes = np.asarray(pts["strike"].to_list(), dtype=np.float32)
        model = np.asarray(pts["model_price"].to_list(), dtype=np.float32)
        market = np.asarray(pts["market_mid"].to_list(), dtype=np.float32)
        error = np.asarray(pts["price_error"].to_list(), dtype=np.float32)
        line_series = {
            "model_price": np.ascontiguousarray(np.column_stack([strikes, model]), dtype=np.float32),
            "market_mid": np.ascontiguousarray(np.column_stack([strikes, market]), dtype=np.float32),
        }
        error_series = {"price_error": np.ascontiguousarray(np.column_stack([strikes, error]), dtype=np.float32)}

    return {
        "line_series": line_series,
        "error_series": error_series,
        "meta": {
            "status": "ok",
            "rows": int(filt.height),
            "mode": mode,
            "data_source": _snapshot_source(snapshot, "routed_greeks"),
            "chart_explanation": (
                "Upper plot: model_price and market_mid against strike for one expiry. "
                f"Lower plot: {'(model-market)/|market|' if relative else 'model-market'} against strike."
            ),
        },
    }

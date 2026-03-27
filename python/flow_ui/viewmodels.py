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
    partition_cols = [c for c in ("option_type", engine_col) if c in filt.columns]
    if partition_cols:
        for group_key, sub in filt.partition_by(partition_cols, as_dict=True).items():
            if not isinstance(group_key, tuple):
                group_key = (group_key,)
            name = ":".join(str(part) for part in group_key if str(part))
            pts = sub.select(["strike", greek]).sort("strike")
            x = _x_transform(np.asarray(pts["strike"].to_list(), dtype=np.float32), forward=forward, space_mode=space_mode)
            y = np.asarray(pts[greek].to_list(), dtype=np.float32)
            arr = np.column_stack([x, y])
            line_series[name] = np.ascontiguousarray(arr, dtype=np.float32)

    heat_frame = filt
    heat_status = "ok"
    if option_type == "all" and "option_type" in filt.columns:
        option_values = sorted({str(x) for x in filt["option_type"].drop_nulls().to_list()})
        if len(option_values) > 1:
            heat_frame = filt.filter(pl.col("option_type") == option_values[0])
            heat_status = f"heatmap_single_option:{option_values[0]}"
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
    snapshot: SymbolSnapshot | pl.DataFrame,
    option_type: str,
    expiry_filter: str,
    *,
    engine_mask: set[str] | None = None,
    relative: bool = False,
) -> dict[str, Any]:
    if isinstance(snapshot, SymbolSnapshot):
        greeks = snapshot.greeks
        data_source = _snapshot_source(snapshot, "routed_greeks")
    else:
        greeks = _latest_batch_frame(snapshot)
        data_source = "surface_points"
    if greeks.is_empty():
        return {
            "line_series": {},
            "error_series": {},
            "meta": {
                "status": "missing_greeks",
                "chart_explanation": "No routed-Greeks rows are available for model-versus-market comparison.",
                "data_source": data_source,
            },
        }

    filt = greeks
    if option_type != "all" and "option_type" in filt.columns:
        filt = filt.filter(pl.col("option_type") == option_type)
    if expiry_filter != "all" and "expiration" in filt.columns:
        filt = filt.filter(pl.col("expiration").cast(pl.String) == expiry_filter)
    if engine_mask and "engine_used" in filt.columns:
        filt = filt.filter(pl.col("engine_used").is_in(sorted(engine_mask)))
    rename_exprs: list[pl.Expr] = []
    if "american_model_price" in filt.columns and "model_price" not in filt.columns:
        rename_exprs.append(pl.col("american_model_price").alias("model_price"))
    if "bid" in filt.columns and "market_bid" not in filt.columns:
        rename_exprs.append(pl.col("bid").alias("market_bid"))
    if "ask" in filt.columns and "market_ask" not in filt.columns:
        rename_exprs.append(pl.col("ask").alias("market_ask"))
    if rename_exprs:
        filt = filt.with_columns(rename_exprs)
    price_label = "american_model_price" if "american_model_price" in greeks.columns else "model_price"
    required = {"strike", "model_price"}
    if not required.issubset(filt.columns):
        return {
            "line_series": {},
            "error_series": {},
            "meta": {
                "status": "missing_columns",
                "chart_explanation": "The routed-Greeks table is missing model or market price columns.",
                "data_source": data_source,
            },
        }
    filt = filt.filter(
        pl.col("strike").is_not_null()
        & pl.col("model_price").is_not_null()
        & pl.col("model_price").is_finite()
    )
    if "market_mid" in filt.columns:
        filt = filt.filter(pl.col("market_mid").is_null() | pl.col("market_mid").is_finite())
    if "market_bid" in filt.columns:
        filt = filt.filter(pl.col("market_bid").is_null() | pl.col("market_bid").is_finite())
    if "market_ask" in filt.columns:
        filt = filt.filter(pl.col("market_ask").is_null() | pl.col("market_ask").is_finite())
    if filt.is_empty():
        return {
            "line_series": {},
            "error_series": {},
            "meta": {
                "status": "filtered_empty",
                "chart_explanation": "No finite model-versus-market price rows survived the current filters.",
                "data_source": data_source,
            },
        }

    if "expiration" in filt.columns and expiry_filter == "all":
        first_expiry = str(filt["expiration"][0])
        filt = filt.filter(pl.col("expiration").cast(pl.String) == first_expiry)
    mode = "relative" if relative else "absolute"

    def _corridor_error_expr() -> pl.Expr:
        if {"market_bid", "market_ask"}.issubset(filt.columns):
            center = pl.when(pl.col("market_mid").is_not_null() & pl.col("market_mid").is_finite()).then(pl.col("market_mid")).otherwise(
                (pl.col("market_bid") + pl.col("market_ask")) / 2.0
            )
            abs_err = (
                pl.when(pl.col("model_price") < pl.col("market_bid"))
                .then(pl.col("model_price") - pl.col("market_bid"))
                .when(pl.col("model_price") > pl.col("market_ask"))
                .then(pl.col("model_price") - pl.col("market_ask"))
                .otherwise(0.0)
            )
            if relative:
                denom = (
                    pl.when((pl.col("market_ask") - pl.col("market_bid")).abs() > 1e-8)
                    .then((pl.col("market_ask") - pl.col("market_bid")).abs())
                    .otherwise(center.abs().clip(1e-8, None))
                )
                return (abs_err / denom).alias("price_error")
            return abs_err.alias("price_error")
        if "market_mid" in filt.columns:
            if relative:
                return (
                    pl.when(pl.col("market_mid").abs() > 1e-8)
                    .then((pl.col("model_price") - pl.col("market_mid")) / pl.col("market_mid").abs())
                    .otherwise(0.0)
                    .alias("price_error")
                )
            return (pl.col("model_price") - pl.col("market_mid")).alias("price_error")
        return pl.lit(0.0).alias("price_error")

    filt = filt.with_columns(_corridor_error_expr())

    group_cols = [c for c in ("option_type", "engine_used") if c in filt.columns]
    grouped = filt.partition_by(group_cols, as_dict=True) if group_cols else {("all",): filt}
    line_series: dict[str, np.ndarray] = {}
    error_series: dict[str, np.ndarray] = {}
    for group_key, sub in grouped.items():
        if not isinstance(group_key, tuple):
            group_key = (group_key,)
        name_parts = [str(part) for part in group_key if str(part)]
        series_prefix = ":".join(name_parts) if name_parts else "series"
        cols = [c for c in ("strike", "market_bid", "market_ask", "market_mid", "model_price", "price_error") if c in sub.columns]
        pts = sub.select(cols).sort("strike")
        strikes = np.asarray(pts["strike"].to_list(), dtype=np.float32)
        if "market_bid" in pts.columns:
            bid = np.asarray(pts["market_bid"].fill_null(float("nan")).to_list(), dtype=np.float32)
            line_series[f"{series_prefix}:bid"] = np.ascontiguousarray(np.column_stack([strikes, bid]), dtype=np.float32)
        if "market_ask" in pts.columns:
            ask = np.asarray(pts["market_ask"].fill_null(float("nan")).to_list(), dtype=np.float32)
            line_series[f"{series_prefix}:ask"] = np.ascontiguousarray(np.column_stack([strikes, ask]), dtype=np.float32)
        if "market_mid" in pts.columns:
            market = np.asarray(pts["market_mid"].fill_null(float("nan")).to_list(), dtype=np.float32)
            line_series[f"{series_prefix}:market_mid"] = np.ascontiguousarray(np.column_stack([strikes, market]), dtype=np.float32)
        model = np.asarray(pts["model_price"].to_list(), dtype=np.float32)
        error = np.asarray(pts["price_error"].to_list(), dtype=np.float32)
        line_series[f"{series_prefix}:model_price"] = np.ascontiguousarray(np.column_stack([strikes, model]), dtype=np.float32)
        error_series[f"{series_prefix}:corridor_error"] = np.ascontiguousarray(np.column_stack([strikes, error]), dtype=np.float32)

    return {
        "line_series": line_series,
        "error_series": error_series,
        "meta": {
            "status": "ok",
            "rows": int(filt.height),
            "mode": mode,
            "data_source": data_source,
            "chart_explanation": (
                f"Upper plot: bid, ask, market_mid, and {price_label} against strike for one expiry. "
                f"Lower plot: {'relative' if relative else 'absolute'} corridor error against strike."
            ),
        },
    }


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
    sort_cols = [c for c in ("expiration", "strike") if c in out.columns]
    if sort_cols:
        out = out.sort(sort_cols)
    return out


def build_surface_validation_payload(
    frame: pl.DataFrame,
    *,
    metric: str,
    option_type: str,
    expiry_filter: str,
) -> dict[str, Any]:
    latest = _latest_batch_frame(frame)
    if latest.is_empty():
        return {
            "line_series": {},
            "heat_image": np.ascontiguousarray(np.zeros((1, 1), dtype=np.float32)),
            "rect": (0.0, 0.0, 1.0, 1.0),
            "levels": (0.0, 1.0),
            "meta": {"status": "missing_surface_points", "chart_explanation": "No surface diagnostics are available yet."},
        }

    filt = latest
    if option_type != "all" and "option_type" in filt.columns:
        filt = filt.filter(pl.col("option_type") == option_type)
    if filt.is_empty():
        return {
            "line_series": {},
            "heat_image": np.ascontiguousarray(np.zeros((1, 1), dtype=np.float32)),
            "rect": (0.0, 0.0, 1.0, 1.0),
            "levels": (0.0, 1.0),
            "meta": {"status": "filtered_empty", "chart_explanation": "No rows survived the selected option-type filter."},
        }

    expiry_choice = expiry_filter
    if "expiration" in filt.columns and expiry_choice == "all":
        expiries = sorted({str(x) for x in filt["expiration"].to_list() if x is not None})
        expiry_choice = expiries[0] if expiries else "all"
    if "expiration" in filt.columns and expiry_choice != "all":
        slice_frame = filt.filter(pl.col("expiration").cast(pl.String) == expiry_choice)
    else:
        slice_frame = filt

    heat_option = option_type
    if option_type == "all" and "option_type" in filt.columns:
        option_values = sorted({str(x) for x in filt["option_type"].drop_nulls().to_list()})
        if len(option_values) > 1:
            heat_option = option_values[0]
            filt = filt.filter(pl.col("option_type") == heat_option)

    line_series: dict[str, np.ndarray] = {}

    def _partition_series(frame_: pl.DataFrame) -> dict[tuple[str, ...], pl.DataFrame]:
        cols = [c for c in ("option_type",) if c in frame_.columns]
        if not cols:
            return {("series",): frame_}
        groups: dict[tuple[str, ...], pl.DataFrame] = {}
        for key, sub in frame_.partition_by(cols, as_dict=True).items():
            parts = key if isinstance(key, tuple) else (key,)
            groups[tuple(str(part) for part in parts)] = sub
        return groups

    def _push_series(name: str, strikes: np.ndarray, values: np.ndarray) -> None:
        line_series[name] = np.ascontiguousarray(np.column_stack([strikes, values]), dtype=np.float32)

    if metric == "implied_vol":
        for name_parts, sub in _partition_series(slice_frame).items():
            cols = [c for c in ("strike", "iv_bid", "iv_ask", "iv_ref", "vendor_iv_ref", "model_implied_vol") if c in sub.columns]
            if "strike" not in cols:
                continue
            ordered = sub.select(cols).sort("strike")
            strikes = np.asarray(ordered["strike"].to_list(), dtype=np.float32)
            prefix = ":".join(name_parts) if name_parts else "series"
            for field in ("iv_bid", "iv_ask", "iv_ref", "vendor_iv_ref", "model_implied_vol"):
                if field in ordered.columns:
                    values = np.asarray(ordered[field].to_list(), dtype=np.float32)
                    _push_series(f"{prefix}:{field}", strikes, values)
        heat_col = "model_implied_vol"
    elif metric == "price":
        for name_parts, sub in _partition_series(slice_frame).items():
            cols = [c for c in ("strike", "bid", "ask", "market_mid", "american_model_price", "model_price") if c in sub.columns]
            if "strike" not in cols:
                continue
            ordered = sub.select(cols).sort("strike")
            strikes = np.asarray(ordered["strike"].to_list(), dtype=np.float32)
            prefix = ":".join(name_parts) if name_parts else "series"
            for field in ("bid", "ask", "market_mid", "american_model_price", "model_price"):
                if field in ordered.columns:
                    values = np.asarray(ordered[field].to_list(), dtype=np.float32)
                    _push_series(f"{prefix}:{field}", strikes, values)
        heat_col = "american_model_price" if "american_model_price" in filt.columns else "model_price"
    else:
        heat_col = metric
        if {"strike", heat_col}.issubset(slice_frame.columns):
            for name_parts, sub in _partition_series(slice_frame).items():
                ordered = sub.select(["strike", heat_col]).sort("strike")
                strikes = np.asarray(ordered["strike"].to_list(), dtype=np.float32)
                values = np.asarray(ordered[heat_col].to_list(), dtype=np.float32)
                prefix = ":".join(name_parts) if name_parts else "series"
                _push_series(f"{prefix}:{heat_col}", strikes, values)

    if heat_col not in filt.columns:
        return {
            "line_series": line_series,
            "heat_image": np.ascontiguousarray(np.zeros((1, 1), dtype=np.float32)),
            "rect": (0.0, 0.0, 1.0, 1.0),
            "levels": (0.0, 1.0),
            "meta": {"status": "missing_metric", "chart_explanation": f"{metric} is not available in the surface diagnostics frame."},
        }

    heat_frame = filt.filter(pl.col(heat_col).is_not_null() & pl.col(heat_col).is_finite())
    if heat_frame.is_empty():
        return {
            "line_series": line_series,
            "heat_image": np.ascontiguousarray(np.zeros((1, 1), dtype=np.float32)),
            "rect": (0.0, 0.0, 1.0, 1.0),
            "levels": (0.0, 1.0),
            "meta": {"status": "metric_empty", "chart_explanation": f"No finite rows are available for {metric}."},
        }

    mat, strike_vals, exp_unique, y_vals = _grid_from_frame(heat_frame.rename({heat_col: "_value"}), "_value")
    mat = np.ascontiguousarray(mat, dtype=np.float32)
    levels = _robust_levels(mat)
    x_vals = np.asarray(strike_vals, dtype=np.float32)
    x_min = float(np.min(x_vals))
    x_max = float(np.max(x_vals))
    y_min = float(min(y_vals)) if y_vals else 0.0
    y_max = float(max(y_vals)) if y_vals else 0.0
    rect = (x_min, y_min - 0.5 if len(y_vals) <= 1 else y_min, max(x_max - x_min, 1e-6), 1.0 if len(y_vals) <= 1 else max(y_max - y_min, 1.0))
    return {
        "line_series": line_series,
        "heat_image": mat,
        "rect": rect,
        "levels": levels,
        "meta": {
            "status": "ok",
            "metric": metric,
            "selected_expiry": expiry_choice,
            "rows": int(heat_frame.height),
            "is_single_expiry": len(exp_unique) <= 1,
            "y_axis_values": [float(x) for x in y_vals],
            "y_axis_labels": [str(x) for x in exp_unique],
            "chart_explanation": (
                f"Slice Explorer: latest-batch {metric} against strike for expiry={expiry_choice}. "
                f"Surface Explorer: {metric} across strike and expiry for the latest batch."
                f"{' Heatmap hidden because only one expiry is active.' if len(exp_unique) <= 1 else ''}"
            ),
        },
    }


def build_density_payload(
    frame: pl.DataFrame,
    *,
    option_type: str,
    expiry_filter: str,
) -> dict[str, Any]:
    latest = _latest_batch_frame(frame)
    if latest.is_empty():
        return {"line_series": {}, "meta": {"status": "missing_surface_points", "chart_explanation": "No surface rows are available for density estimation."}}

    filt = latest
    if option_type != "all" and "option_type" in filt.columns:
        filt = filt.filter(pl.col("option_type") == option_type)
    if "expiration" in filt.columns and expiry_filter != "all":
        filt = filt.filter(pl.col("expiration").cast(pl.String) == expiry_filter)
    elif "expiration" in filt.columns and not filt.is_empty():
        first_expiry = str(filt["expiration"][0])
        filt = filt.filter(pl.col("expiration").cast(pl.String) == first_expiry)

    if filt.is_empty() or not {"strike", "model_price"}.issubset(filt.columns) or filt.height < 3:
        return {
            "line_series": {},
            "meta": {
                "status": "insufficient_density_points",
                "chart_explanation": "Risk-neutral density requires at least three sorted strike points with model prices.",
            },
        }

    ordered = filt.select(["strike", "model_price"]).sort("strike")
    ordered = ordered.group_by("strike").agg(pl.mean("model_price").alias("model_price")).sort("strike")
    if ordered.height < 3:
        return {
            "line_series": {},
            "meta": {
                "status": "insufficient_unique_strikes",
                "chart_explanation": "Risk-neutral density requires at least three unique strike points after consolidating duplicate strikes.",
            },
        }

    strikes = np.asarray(ordered["strike"].to_list(), dtype=np.float64)
    if np.any(np.diff(strikes) <= 0.0):
        return {
            "line_series": {},
            "meta": {
                "status": "duplicate_strike_grid",
                "chart_explanation": "Risk-neutral density is unavailable because the strike grid is not strictly increasing.",
            },
        }

    prices = np.asarray(ordered["model_price"].to_list(), dtype=np.float64)
    first = np.gradient(prices, strikes)
    second = np.gradient(first, strikes)
    density = second.astype(np.float32, copy=False)
    integral = float(np.trapezoid(density, strikes))
    negatives = int(np.sum(density < 0.0))
    return {
        "line_series": {
            "density": np.ascontiguousarray(np.column_stack([strikes.astype(np.float32), density]), dtype=np.float32),
        },
        "meta": {
            "status": "ok",
            "integral": integral,
            "negative_points": negatives,
            "chart_explanation": "Approximate risk-neutral density from the second strike derivative of latest-batch model prices.",
        },
    }





def build_overlay_frame_payload(
    frame: pl.DataFrame,
    greek: str,
    option_type: str,
    expiry_filter: str,
    *,
    space_mode: str = "strike",
    engine_mask: set[str] | None = None,
    dual_mode: bool = False,
    data_source: str = "greeks",
) -> dict[str, Any]:
    if frame.is_empty() or greek not in frame.columns:
        payload = _empty_overlay("missing_greeks")
        payload["meta"]["data_source"] = f"{data_source} (empty)"
        return payload

    filt = frame
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
        payload = _empty_overlay("missing_columns")
        payload["meta"]["data_source"] = data_source
        return payload
    filt = filt.filter(pl.col("strike").is_not_null() & pl.col(greek).is_not_null() & pl.col(greek).is_finite())
    if filt.is_empty():
        payload = _empty_overlay("filtered_empty")
        payload["meta"]["data_source"] = data_source
        return payload

    forward = float(filt["underlying_price"][0]) if "underlying_price" in filt.columns else 1.0
    engine_col = "engine_used" if "engine_used" in filt.columns else "greeks_engine"
    line_series: dict[str, np.ndarray] = {}
    engine_values = sorted({str(x) for x in filt[engine_col].drop_nulls().to_list()}) if engine_col in filt.columns else []
    partition_cols = [c for c in ("option_type", engine_col) if c in filt.columns]
    if partition_cols:
        for group_key, sub in filt.partition_by(partition_cols, as_dict=True).items():
            if not isinstance(group_key, tuple):
                group_key = (group_key,)
            name = ":".join(str(part) for part in group_key if str(part))
            pts = sub.select(["strike", greek]).sort("strike")
            x = _x_transform(np.asarray(pts["strike"].to_list(), dtype=np.float32), forward=forward, space_mode=space_mode)
            y = np.asarray(pts[greek].to_list(), dtype=np.float32)
            line_series[name] = np.ascontiguousarray(np.column_stack([x, y]), dtype=np.float32)

    heat_frame = filt
    heat_status = "ok"
    if option_type == "all" and "option_type" in filt.columns:
        option_values = sorted({str(x) for x in filt["option_type"].drop_nulls().to_list()})
        if len(option_values) > 1:
            heat_frame = filt.filter(pl.col("option_type") == option_values[0])
            heat_status = f"heatmap_single_option:{option_values[0]}"
    if len(engine_values) > 1 and engine_col in filt.columns:
        heat_frame = filt.filter(pl.col(engine_col) == engine_values[0])
        heat_status = f"heatmap_single_engine:{engine_values[0]}"

    if space_mode == "residual" and "space_mode" in heat_frame.columns:
        left = heat_frame.filter(pl.col("space_mode") == "log").group_by(["expiration", "strike"]).agg(pl.mean(greek).alias("log_val"))
        right = heat_frame.filter(pl.col("space_mode") == "strike").group_by(["expiration", "strike"]).agg(pl.mean(greek).alias("strike_val"))
        joined = left.join(right, on=["expiration", "strike"], how="inner")
        if joined.is_empty():
            payload = _empty_overlay("residual_empty")
            payload["meta"]["data_source"] = data_source
            return payload
        heat_frame = joined.with_columns((pl.col("strike_val") - pl.col("log_val")).alias(greek))

    mat, strike_vals, exp_unique, y_vals = _grid_from_frame(heat_frame.rename({greek: "_value"}), "_value")
    mat = np.ascontiguousarray(mat, dtype=np.float32)
    levels = _robust_levels(mat)
    x_vals = np.asarray(strike_vals, dtype=np.float32)
    if space_mode == "log":
        x_vals = _x_transform(x_vals, forward=forward, space_mode=space_mode)
    x_min = float(np.min(x_vals))
    x_max = float(np.max(x_vals))
    y_min = float(min(y_vals)) if y_vals else 0.0
    y_max = float(max(y_vals)) if y_vals else 0.0
    payload = {
        "line_series": line_series,
        "heat_image": mat,
        "rect": (x_min, y_min - 0.5 if len(y_vals) <= 1 else y_min, max(x_max - x_min, 1e-6), 1.0 if len(y_vals) <= 1 else max(y_max - y_min, 1.0)),
        "levels": levels,
        "meta": {
            "status": heat_status,
            "rows": int(filt.height),
            "space_mode": space_mode,
            "payload_bytes": int(mat.nbytes + sum(arr.nbytes for arr in line_series.values())),
            "y_axis_mode": "days_to_expiry",
            "y_axis_values": [float(x) for x in y_vals],
            "y_axis_labels": [str(x) for x in exp_unique],
            "is_single_expiry": len(exp_unique) <= 1,
            "expiries_loaded": exp_unique,
            "chart_explanation": f"Overlay of {data_source} against strike/log-moneyness for the selected scope.",
            "data_source": data_source,
            "heat_engine": engine_values[0] if engine_values else "",
        },
    }
    if dual_mode:
        payload["heat_image_alt"] = mat.copy()
        payload["rect_alt"] = payload["rect"]
    return payload


def build_processing_trace_payload(
    frame: pl.DataFrame,
    *,
    option_type: str,
    expiry_filter: str,
) -> dict[str, Any]:
    latest = _latest_batch_frame(frame)
    if latest.is_empty():
        return {"panels": {}, "meta": {"status": "missing_surface_points", "chart_explanation": "No surface rows are available for processing trace."}}

    filt = latest
    expiry_choice = expiry_filter
    if "expiration" in filt.columns and expiry_choice == "all":
        expiries = sorted({str(x) for x in filt["expiration"].to_list() if x is not None})
        expiry_choice = expiries[0] if expiries else "all"
    if "expiration" in filt.columns and expiry_choice != "all":
        filt = filt.filter(pl.col("expiration").cast(pl.String) == expiry_choice)
    if option_type != "all" and "option_type" in filt.columns:
        filt = filt.filter(pl.col("option_type") == option_type)
    if filt.is_empty():
        return {"panels": {}, "meta": {"status": "filtered_empty", "chart_explanation": "No rows survived the selected expiry/option filter."}}

    def panel_series(
        frame_: pl.DataFrame,
        field_map: dict[str, str],
        *,
        filter_expr=None,
    ) -> dict[str, np.ndarray]:
        sub = frame_.filter(filter_expr) if filter_expr is not None else frame_
        out: dict[str, np.ndarray] = {}
        if sub.is_empty() or "strike" not in sub.columns:
            return out
        groups = sub.partition_by([c for c in ("option_type",) if c in sub.columns], as_dict=True) if "option_type" in sub.columns else {("series",): sub}
        for key, group in groups.items():
            prefix = ":".join(str(part) for part in (key if isinstance(key, tuple) else (key,)))
            ordered = group.sort("strike")
            strikes = np.asarray(ordered["strike"].to_list(), dtype=np.float32)
            for field, label in field_map.items():
                if field not in ordered.columns:
                    continue
                values = np.asarray(ordered[field].to_list(), dtype=np.float32)
                out[f"{prefix}:{label}"] = np.ascontiguousarray(np.column_stack([strikes, values]), dtype=np.float32)
        return out

    dropped = filt.filter(pl.col("drop_reason").cast(pl.String).str.len_chars() > 0) if "drop_reason" in filt.columns else pl.DataFrame()
    fit_excluded = (
        filt.filter(pl.col("eligible_for_fit") == False)
        if "eligible_for_fit" in filt.columns
        else pl.DataFrame()
    )
    processing = {
        "cleaning": {
            "line_series": {
                **panel_series(filt, {"bid": "raw_bid", "ask": "raw_ask"}),
                **panel_series(
                    filt,
                    {"bid": "clean_bid", "ask": "clean_ask"},
                    filter_expr=pl.col("eligible_prestrip") == True,
                ),
                **panel_series(
                    dropped.with_columns(pl.col("market_mid").alias("dropped_mid")),
                    {"dropped_mid": "dropped_mid"},
                ),
            },
            "title": "Cleaning",
        },
        "deamericanization": {
            "line_series": {
                **panel_series(
                    filt,
                    {"bid": "clean_bid", "ask": "clean_ask"},
                    filter_expr=pl.col("eligible_prestrip") == True,
                ),
                **panel_series(
                    filt,
                    {
                        "euro_price_bid": "euro_bid",
                        "euro_price_ask": "euro_ask",
                        "euro_price_ref": "euro_ref",
                    },
                ),
            },
            "title": "De-Americanization",
        },
        "vol_fit": {
            "line_series": panel_series(
                filt,
                {
                    "iv_bid": "iv_bid",
                    "iv_ask": "iv_ask",
                    "iv_ref": "iv_ref",
                    "ssvi_vol": "ssvi_vol",
                },
            ),
            "title": "Vol Fit Corridor",
        },
        "vol_diag": {
            "line_series": {
                **panel_series(
                    filt,
                    {
                        "iv_bid": "iv_bid",
                        "iv_ask": "iv_ask",
                        "iv_ref": "iv_ref",
                        "ssvi_vol": "ssvi_vol",
                        "vendor_iv_ref": "vendor_iv_ref",
                    },
                ),
                **panel_series(
                    fit_excluded.with_columns(pl.col("iv_ref").alias("excluded_iv_ref")),
                    {"excluded_iv_ref": "excluded_iv_ref"},
                ),
            },
            "title": "Full Vol Diagnostic",
        },
        "euro_reprice": {
            "line_series": panel_series(
                filt,
                {
                    "euro_price_bid": "euro_bid",
                    "euro_price_ask": "euro_ask",
                    "euro_price_ref": "euro_ref",
                    "ssvi_euro_price": "ssvi_euro_price",
                },
            ),
            "title": "European Reprice",
        },
        "american_reprice": {
            "line_series": panel_series(
                filt,
                {
                    "bid": "clean_bid",
                    "ask": "clean_ask",
                    "market_mid": "market_mid",
                    "ssvi_american_price": "ssvi_american_price",
                },
            ),
            "title": "American Reprice",
        },
    }
    return {
        "panels": processing,
        "meta": {
            "status": "ok",
            "selected_expiry": expiry_choice,
            "rows": int(filt.height),
            "chart_explanation": "Trace the batch-scoped processing path from cleaned American prices through de-Americanization, SSVI fit validation, and American repricing.",
        },
    }
def build_calendar_payload(frame: pl.DataFrame, *, option_type: str) -> dict[str, Any]:
    latest = _latest_batch_frame(frame)
    if latest.is_empty():
        return {
            "heat_image": np.ascontiguousarray(np.zeros((1, 1), dtype=np.float32)),
            "rect": (0.0, 0.0, 1.0, 1.0),
            "levels": (0.0, 1.0),
            "meta": {"status": "missing_surface_points"},
        }
    filt = latest
    if option_type != "all" and "option_type" in filt.columns:
        filt = filt.filter(pl.col("option_type") == option_type)
    if "calendar_total_variance" not in filt.columns:
        return {
            "heat_image": np.ascontiguousarray(np.zeros((1, 1), dtype=np.float32)),
            "rect": (0.0, 0.0, 1.0, 1.0),
            "levels": (0.0, 1.0),
            "meta": {"status": "missing_calendar_metric"},
        }
    heat_frame = filt.filter(pl.col("calendar_total_variance").is_not_null() & pl.col("calendar_total_variance").is_finite())
    if heat_frame.is_empty():
        return {
            "heat_image": np.ascontiguousarray(np.zeros((1, 1), dtype=np.float32)),
            "rect": (0.0, 0.0, 1.0, 1.0),
            "levels": (0.0, 1.0),
            "meta": {"status": "calendar_metric_empty"},
        }
    mat, strike_vals, exp_unique, y_vals = _grid_from_frame(
        heat_frame.rename({"calendar_total_variance": "_value"}),
        "_value",
    )
    mat = np.ascontiguousarray(mat, dtype=np.float32)
    levels = _robust_levels(mat)
    x_min = float(min(strike_vals))
    x_max = float(max(strike_vals))
    y_min = float(min(y_vals)) if y_vals else 0.0
    y_max = float(max(y_vals)) if y_vals else 0.0
    return {
        "heat_image": mat,
        "rect": (x_min, y_min - 0.5 if len(y_vals) <= 1 else y_min, max(x_max - x_min, 1e-6), 1.0 if len(y_vals) <= 1 else max(y_max - y_min, 1.0)),
        "levels": levels,
        "meta": {
            "status": "ok",
            "rows": int(heat_frame.height),
            "violation_count": int(heat_frame.filter(pl.col("calendar_violation") == True).height) if "calendar_violation" in heat_frame.columns else 0,
            "is_single_expiry": len(exp_unique) <= 1,
            "y_axis_values": [float(x) for x in y_vals],
            "y_axis_labels": [str(x) for x in exp_unique],
            "chart_explanation": (
                "Calendar Inspector: total variance across strike and expiry for the latest batch."
                f"{' Heatmap hidden because only one expiry is active.' if len(exp_unique) <= 1 else ''}"
            ),
        },
    }


def build_runtime_metrics_payload(frame: pl.DataFrame) -> dict[str, Any]:
    if frame.is_empty():
        return {
            "line_series": {},
            "meta": {"status": "missing_runtime_metrics", "chart_explanation": "No runtime metrics have been recorded yet."},
        }
    ordered = frame.sort([c for c in ("asof_ts", "version") if c in frame.columns]).tail(60)
    x = np.arange(ordered.height, dtype=np.float32)
    metrics = ["total_ms", "calibration_ms", "pricing_ms", "routing_ms"]
    line_series: dict[str, np.ndarray] = {}
    for metric in metrics:
        if metric not in ordered.columns:
            continue
        y = np.asarray(ordered[metric].to_list(), dtype=np.float32)
        line_series[metric] = np.ascontiguousarray(np.column_stack([x, y]), dtype=np.float32)
    latest = ordered.to_dicts()[-1]
    return {
        "line_series": line_series,
        "meta": {
            "status": "ok",
            "rows": int(ordered.height),
            "latest_total_ms": float(latest.get("total_ms", 0.0)),
            "chart_explanation": "Recent batch latency trends for runtime stages.",
        },
    }


def build_short_expiry_scanner_payload(
    focus_summary: pl.DataFrame,
    dealer_exposure_points: pl.DataFrame,
    scanner_levels: pl.DataFrame,
    flow_proxy_points: pl.DataFrame,
    *,
    selected_focus_label: str | None = None,
) -> dict[str, Any]:
    latest_summary = _latest_batch_frame(focus_summary)
    latest_dealer = _latest_batch_frame(dealer_exposure_points)
    latest_levels = _latest_batch_frame(scanner_levels)
    latest_flow = _latest_batch_frame(flow_proxy_points)
    if latest_summary.is_empty():
        return {
            "heat_image": np.ascontiguousarray(np.zeros((1, 1), dtype=np.float32)),
            "rect": (0.0, 0.0, 1.0, 1.0),
            "levels": (0.0, 1.0),
            "summary_frame": pl.DataFrame(),
            "levels_frame": pl.DataFrame(),
            "flow_frame": pl.DataFrame(),
            "meta": {
                "status": "missing_focus_expiry_summary",
                "chart_explanation": "No focused short-expiry scanner summary is available yet.",
            },
        }

    ordered_summary = latest_summary.sort([c for c in ("focus_order", "expiration") if c in latest_summary.columns])
    available_labels = [str(value) for value in ordered_summary["focus_label"].to_list()]
    selected = selected_focus_label if selected_focus_label in available_labels else (available_labels[0] if available_labels else "")
    summary_slice = ordered_summary.filter(pl.col("focus_label") == selected).head(1)

    dealer_latest = latest_dealer
    heat_rows = (
        dealer_latest.group_by(["focus_label", "strike"])
        .agg(pl.sum("gamma_exposure_oi").alias("gamma_exposure_oi"))
        .sort(["focus_label", "strike"])
        if not dealer_latest.is_empty() and {"focus_label", "strike", "gamma_exposure_oi"}.issubset(dealer_latest.columns)
        else pl.DataFrame()
    )
    labels = available_labels or ["n/a"]
    strike_vals = sorted({float(value) for value in heat_rows["strike"].to_list()}) if not heat_rows.is_empty() else [0.0]
    mat = np.zeros((len(labels), len(strike_vals)), dtype=np.float32)
    label_index = {label: idx for idx, label in enumerate(labels)}
    strike_index = {value: idx for idx, value in enumerate(strike_vals)}
    if not heat_rows.is_empty():
        for row in heat_rows.to_dicts():
            mat[label_index[str(row["focus_label"])], strike_index[float(row["strike"])]] = float(row["gamma_exposure_oi"])
    levels = _robust_levels(mat)
    rect = (
        float(min(strike_vals)),
        -0.5 if len(labels) <= 1 else 0.0,
        max(float(max(strike_vals) - min(strike_vals)), 1e-6),
        1.0 if len(labels) <= 1 else float(max(len(labels) - 1, 1)),
    )
    ladder = (
        latest_levels.filter(pl.col("focus_label") == selected).sort("hotspot_score", descending=True).head(25)
        if not latest_levels.is_empty() and "focus_label" in latest_levels.columns
        else pl.DataFrame()
    )
    flow = (
        latest_flow.filter(pl.col("focus_label") == selected).sort("proxy_confidence", descending=True).head(25)
        if not latest_flow.is_empty() and "focus_label" in latest_flow.columns
        else pl.DataFrame()
    )
    trust_status = str(summary_slice["trust_status"][0]) if not summary_slice.is_empty() and "trust_status" in summary_slice.columns else "n/a"
    trust_score = float(summary_slice["trust_score"][0]) if not summary_slice.is_empty() and "trust_score" in summary_slice.columns else float("nan")
    selected_expiration = (
        str(summary_slice["expiration"][0]) if not summary_slice.is_empty() and "expiration" in summary_slice.columns else "n/a"
    )
    snapshot_age_sec = (
        float(summary_slice["snapshot_age_sec"][0])
        if not summary_slice.is_empty() and "snapshot_age_sec" in summary_slice.columns
        else float("nan")
    )
    return {
        "heat_image": np.ascontiguousarray(mat, dtype=np.float32),
        "rect": rect,
        "levels": levels,
        "summary_frame": ordered_summary,
        "levels_frame": ladder,
        "flow_frame": flow,
        "meta": {
            "status": "ok",
            "selected_focus_label": selected,
            "selected_expiration": selected_expiration,
            "trust_status": trust_status,
            "trust_score": trust_score,
            "snapshot_age_sec": snapshot_age_sec,
            "y_axis_values": [float(idx) for idx, _label in enumerate(labels)],
            "y_axis_labels": labels,
            "chart_explanation": (
                "Scanner heatmap shows net gamma OI exposure across strike for the focused short expiries. "
                "Use the expiry cards to drive the drilldown pages, and treat flow proxy rows as snapshot-to-snapshot heuristics."
            ),
        },
    }


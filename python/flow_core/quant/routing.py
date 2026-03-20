from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

import polars as pl


@dataclass(slots=True)
class RouteDecision:
    days_to_expiry: int
    iv_engine: str
    greeks_engine: str
    dividend_policy: str
    rationale: str


def route_expiry_bucket(days_to_expiry: int) -> RouteDecision:
    if days_to_expiry <= 4:
        return RouteDecision(
            days_to_expiry=days_to_expiry,
            iv_engine="bjerksund_stensland",
            greeks_engine="crank_nicolson_fdm",
            dividend_policy="node_event_exact",
            rationale="0DTE-EOW boundary stabilization with event-exact discrete dividends",
        )
    if days_to_expiry < 31:
        return RouteDecision(
            days_to_expiry=days_to_expiry,
            iv_engine="bjerksund_stensland",
            greeks_engine="binomial_richardson",
            dividend_policy="node_event_exact",
            rationale="weekly tenor ex-div node alignment with q=0 in CRR step",
        )
    if days_to_expiry < 366:
        return RouteDecision(
            days_to_expiry=days_to_expiry,
            iv_engine="bjerksund_stensland",
            greeks_engine="bjerksund_stensland",
            dividend_policy="escrowed",
            rationale="monthly medium-tenor analytical approximation with escrowed div transform",
        )
    if days_to_expiry <= 1095:
        return RouteDecision(
            days_to_expiry=days_to_expiry,
            iv_engine="luba_2point",
            greeks_engine="rim",
            dividend_policy="escrowed",
            rationale="long tenor integral methods with escrowed div transform",
        )
    return RouteDecision(
        days_to_expiry=days_to_expiry,
        iv_engine="rim",
        greeks_engine="laplace_transform_zhu",
        dividend_policy="escrowed",
        rationale="leaps Laplace-domain engine with escrowed div transform",
    )


def _compute_days_to_expiry(expiration: date, asof: datetime | None) -> int:
    asof_dt = asof or datetime.now(timezone.utc)
    if asof_dt.tzinfo is None:
        asof_dt = asof_dt.replace(tzinfo=timezone.utc)
    delta = (expiration - asof_dt.date()).days
    return max(delta, 0)


def annotate_with_routing(frame: pl.DataFrame) -> pl.DataFrame:
    if frame.is_empty() or "expiration" not in frame.columns:
        return frame

    rows = frame.to_dicts()
    routed_rows = []
    for row in rows:
        expiration = row.get("expiration")
        asof_ts = row.get("asof_ts")
        if expiration is None:
            routed_rows.append(row)
            continue
        days = _compute_days_to_expiry(expiration, asof_ts)
        decision = route_expiry_bucket(days)
        row["days_to_expiry"] = decision.days_to_expiry
        row["iv_engine"] = decision.iv_engine
        row["greeks_engine"] = decision.greeks_engine
        row["dividend_policy"] = decision.dividend_policy
        row["routing_rationale"] = decision.rationale
        routed_rows.append(row)

    return pl.DataFrame(routed_rows)

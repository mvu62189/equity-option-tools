from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True)
class DividendEvent:
    amount: float
    time_to_ex_date: float


def pv_dividends(divs: list[DividendEvent] | tuple[DividendEvent, ...], rate: float, tau: float) -> float:
    pv = 0.0
    for d in divs:
        t = float(d.time_to_ex_date)
        if 0.0 < t < tau and d.amount > 0.0:
            pv += float(d.amount) * math.exp(-rate * t)
    return pv


def escrowed_spot(
    spot: float,
    rate: float,
    tau: float,
    divs: list[DividendEvent] | tuple[DividendEvent, ...],
) -> float:
    return max(spot - pv_dividends(divs, rate=rate, tau=tau), 1e-12)

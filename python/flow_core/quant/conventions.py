from __future__ import annotations

import math


def terminal_boundary_call(strike: float, rate: float, dividend: float) -> float:
    if strike <= 0.0:
        return 0.0
    if dividend <= 0.0:
        return math.inf
    return max(strike, (rate / dividend) * strike)


def terminal_boundary_put(strike: float, rate: float, dividend: float) -> float:
    if strike <= 0.0:
        return 0.0
    if dividend <= 0.0:
        return strike
    return min(strike, (rate / dividend) * strike)


def theta_one_day_forward(value_t: float, value_t_minus_dt: float, dt: float = 1.0 / 365.0) -> float:
    """Return theta as 1-calendar-day forward decay per day.

    Theta = (V(T-dt) - V(T)) / dt
    """
    if dt <= 0.0:
        return float("nan")
    return (value_t_minus_dt - value_t) / dt

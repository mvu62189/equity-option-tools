from __future__ import annotations

import math

from flow_core.quant.conventions import (
    terminal_boundary_call,
    terminal_boundary_put,
    theta_one_day_forward,
)


def test_terminal_boundaries_match_spec() -> None:
    k = 100.0
    r = 0.05
    q = 0.02
    assert terminal_boundary_call(k, r, q) == max(k, (r / q) * k)
    assert terminal_boundary_put(k, r, q) == min(k, (r / q) * k)


def test_terminal_boundary_call_no_dividend_is_infinite() -> None:
    assert math.isinf(terminal_boundary_call(100.0, 0.03, 0.0))


def test_terminal_boundary_put_drops_below_strike_when_q_gt_r() -> None:
    k = 100.0
    r = 0.05
    q = 0.08
    b_put = terminal_boundary_put(k, r, q)
    assert b_put == (r / q) * k
    assert b_put < k


def test_theta_one_day_forward_convention() -> None:
    theta = theta_one_day_forward(value_t=2.0, value_t_minus_dt=1.95, dt=1.0 / 365.0)
    assert theta < 0

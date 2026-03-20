from __future__ import annotations

from flow_core.quant.conventions import terminal_boundary_put
from flow_core.quant.luba_rim import calc_luba_2pt_put, calc_rim_put


def test_luba_put_boundary_extreme_q_gt_r_stays_below_strike() -> None:
    k = 100.0
    r = 0.05
    q = 0.08
    b_t = terminal_boundary_put(k, r, q)
    assert b_t < k

    price = calc_luba_2pt_put(s=95.0, k=k, t=0.5, r=r, q=q, sigma=0.25)
    assert price > 0.0


def test_rim_put_extreme_q_gt_r_stays_finite() -> None:
    price = calc_rim_put(s=95.0, k=100.0, t=0.5, r=0.05, q=0.08, sigma=0.25, n=48)
    assert price > 0.0

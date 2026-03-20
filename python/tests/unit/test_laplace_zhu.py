from __future__ import annotations

import pytest

from flow_core.quant import laplace_zhu
from flow_core.quant.laplace_zhu import price_laplace_zhu_call, price_laplace_zhu_put


def test_laplace_zhu_requires_even_stehfest_m() -> None:
    with pytest.raises(ValueError):
        price_laplace_zhu_call(s=100.0, k=100.0, tau=3.5, r=0.04, q=0.02, sigma=0.2, m=11)


def test_laplace_zhu_returns_nonnegative_price() -> None:
    price = price_laplace_zhu_call(s=100.0, k=100.0, tau=3.5, r=0.04, q=0.02, sigma=0.2, m=12)
    assert price >= 0.0


def test_laplace_zhu_put_returns_nonnegative_price() -> None:
    price = price_laplace_zhu_put(s=100.0, k=100.0, tau=3.5, r=0.04, q=0.02, sigma=0.2, m=12)
    assert price >= 0.0


def test_put_laplace_boundary_solver_stays_below_strike() -> None:
    k = 100.0
    r = 0.04
    q = 0.02
    sigma = 0.2
    lam = 0.6
    rho1, _ = laplace_zhu._characteristic_roots(r=r, q=q, sigma=sigma, lam=lam)
    boundary = laplace_zhu._solve_b_put_star_lambda(k=k, r=r, q=q, sigma=sigma, lam=lam, rho1=rho1)
    assert 0.0 < boundary <= k


def test_put_laplace_piecewise_value_decreases_with_spot() -> None:
    k = 100.0
    r = 0.04
    q = 0.02
    sigma = 0.2
    lam = 0.5
    low = laplace_zhu._hat_p_eur_laplace(s=90.0, k=k, r=r, q=q, sigma=sigma, lam=lam)
    mid = laplace_zhu._hat_p_eur_laplace(s=100.0, k=k, r=r, q=q, sigma=sigma, lam=lam)
    high = laplace_zhu._hat_p_eur_laplace(s=110.0, k=k, r=r, q=q, sigma=sigma, lam=lam)
    assert low >= mid >= high

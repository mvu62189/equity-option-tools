from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl

from flow_core.quant.deamericanization import evaluate_parity_by_expiry, evaluate_parity_diagnostics


def test_evaluate_parity_by_expiry_returns_winner() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["SPY", "SPY", "SPY", "SPY"],
            "asof_ts": [
                datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
                datetime(2026, 2, 25, 15, 0, tzinfo=timezone.utc),
            ],
            "expiration": [date(2026, 3, 20), date(2026, 3, 20), date(2026, 3, 20), date(2026, 3, 20)],
            "option_type": ["call", "put", "call", "put"],
            "strike": [500.0, 500.0, 510.0, 510.0],
            "bid": [11.8, 1.5, 5.9, 4.7],
            "ask": [12.2, 1.7, 6.1, 4.9],
            "last": [12.0, 1.6, 6.0, 4.8],
            "volume": [0, 0, 0, 0],
            "open_interest": [1, 1, 1, 1],
            "underlying_price": [505.0, 505.0, 505.0, 505.0],
            "implied_vol_vendor": [0.2, 0.2, 0.2, 0.2],
            "provider": ["yfinance", "yfinance", "yfinance", "yfinance"],
            "snapshot_id": ["s1", "s1", "s1", "s1"],
        }
    )

    out = evaluate_parity_by_expiry(frame)
    assert out.height == 1
    assert out["winner_model"][0] in {"bjerksund_stensland", "luba"}
    assert out["pairs"][0] == 2
    assert "winner_gap" in out.columns

    summary, detail = evaluate_parity_diagnostics(frame)
    assert summary.height == 1
    assert detail.height == 4
    assert set(detail["model"].to_list()) == {"bjerksund_stensland", "luba"}

    summary_hybrid, detail_hybrid = evaluate_parity_diagnostics(
        frame,
        eep_mode="hybrid",
        max_pairs=2,
        tree_steps=80,
    )
    assert summary_hybrid.height == 1
    assert detail_hybrid.height == 4

    summary3, detail3, solver_diag = evaluate_parity_diagnostics(
        frame,
        eep_mode="hybrid",
        max_pairs=2,
        tree_steps=80,
        luba_method="rim",
        return_solver_diagnostics=True,
    )
    assert summary3.height == 1
    assert detail3.height == 4
    assert solver_diag.height >= 1
    assert {"model_id", "converged", "iterations", "sse_final", "params"} <= set(solver_diag.columns)

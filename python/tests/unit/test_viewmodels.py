from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace

import numpy as np
import polars as pl

from flow_core.orchestration.state_store import SymbolSnapshot
from flow_ui.viewmodels import build_overlay_payload, build_price_error_payload


def _snapshot() -> SymbolSnapshot:
    greeks = pl.DataFrame(
        {
            "expiration": ["2026-03-20", "2026-03-20", "2026-04-17", "2026-04-17"],
            "option_type": ["call", "call", "call", "call"],
            "engine_used": ["laplace_zhu_cpp", "laplace_zhu_cpp", "laplace_zhu_cpp", "laplace_zhu_cpp"],
            "strike": [90.0, 100.0, 90.0, 100.0],
            "delta": [0.6, 0.5, 0.55, 0.45],
        }
    )
    return SymbolSnapshot(
        symbol="SPY",
        batch_id="b1",
        version=1,
        updated_at_utc=datetime.now(timezone.utc),
        raw=pl.DataFrame(),
        greeks=greeks,
        ssvi=pl.DataFrame(),
        dispatch=pl.DataFrame(),
        parity=pl.DataFrame(),
        parity_detail=pl.DataFrame(),
        calibration_diag_tail=pl.DataFrame(),
        overlay_payloads={},
        memory_bytes={},
        drop_counters={},
        latency_ms={},
        status={},
    )


def test_overlay_payload_is_contiguous_float32() -> None:
    payload = build_overlay_payload(_snapshot(), greek="delta", option_type="call", expiry_filter="all")
    heat = payload["heat_image"]
    assert isinstance(heat, np.ndarray)
    assert heat.dtype == np.float32
    assert heat.flags["C_CONTIGUOUS"]
    assert heat.ndim == 2
    assert payload["meta"]["status"] == "ok"


def test_overlay_payload_supports_space_mode_and_engine_mask() -> None:
    payload = build_overlay_payload(
        _snapshot(),
        greek="delta",
        option_type="call",
        expiry_filter="all",
        space_mode="log",
        engine_mask={"laplace_zhu_cpp"},
        dual_mode=True,
    )
    assert payload["meta"]["space_mode"] == "log"
    assert payload["meta"]["payload_bytes"] >= payload["heat_image"].nbytes
    assert "heat_image_secondary" in payload


def test_price_error_payload_uses_routed_greeks_prices() -> None:
    snapshot = _snapshot()
    snapshot = replace(
        snapshot,
        greeks=snapshot.greeks.with_columns(
            pl.Series("model_price", [1.1, 1.0, 1.2, 1.1]),
            pl.Series("market_mid", [1.0, 0.9, 1.15, 1.05]),
        ),
    )
    payload = build_price_error_payload(snapshot, option_type="call", expiry_filter="2026-03-20", relative=False)
    assert payload["meta"]["status"] == "ok"
    assert "laplace_zhu_cpp:model" in payload["line_series"]
    assert "laplace_zhu_cpp" in payload["error_series"]

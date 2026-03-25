from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from flow_app import legacy
from flow_app.runtime import (
    AppMode,
    LaunchConfig,
    build_arg_parser,
    load_launch_config,
    main,
    merge_cli_overrides,
    save_launch_config,
    session_state_path,
    _run_snapshot_once,
    build_runtime_summary,
)


def test_session_state_path_uses_local_appdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    appdata = tmp_path / "local-appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    assert session_state_path() == appdata / "quant-pipeline-mvp" / "launch_config.json"


def test_save_and_load_launch_config_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    config = LaunchConfig(
        mode=AppMode.HEADLESS_LIVE,
        ticker="qqq",
        expiration="2026-04-17",
        refresh_ms=125,
        allow_shared=True,
        provider_config="configs/providers/custom.yaml",
        pipeline_config="configs/pipeline/research.yaml",
    )
    path = save_launch_config(config)
    assert path.exists()
    assert load_launch_config() == config.normalized()


def test_merge_cli_overrides_normalizes_inputs() -> None:
    args = build_arg_parser().parse_args(
        [
            "--mode",
            "snapshot_once",
            "--ticker",
            " qqq ",
            "--expiration",
            "2026-06-19",
            "--refresh-ms",
            "10",
            "--provider-config",
            " providers.yaml ",
            "--pipeline-config",
            " pipeline.yaml ",
            "--allow-shared",
        ]
    )
    launch = merge_cli_overrides(LaunchConfig(), args)
    assert launch == LaunchConfig(
        mode=AppMode.SNAPSHOT_ONCE,
        ticker="QQQ",
        expiration="2026-06-19",
        refresh_ms=10,
        allow_shared=True,
        provider_config="providers.yaml",
        pipeline_config="pipeline.yaml",
    )


def test_main_uses_cli_mode_without_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, LaunchConfig] = {}

    def _fake_run(launch: LaunchConfig, *, app=None) -> int:  # noqa: ANN001
        captured["launch"] = launch
        return 17

    monkeypatch.setattr("flow_app.runtime.load_launch_config", lambda: LaunchConfig())
    monkeypatch.setattr("flow_app.runtime.run_launch_config", _fake_run)

    result = main(
        [
            "--mode",
            "headless_live",
            "--ticker",
            "iwm",
            "--refresh-ms",
            "250",
            "--allow-shared",
        ]
    )

    assert result == 17
    assert captured["launch"].mode is AppMode.HEADLESS_LIVE
    assert captured["launch"].ticker == "IWM"
    assert captured["launch"].refresh_ms == 250
    assert captured["launch"].allow_shared is True


@pytest.mark.parametrize(
    ("runner", "argv", "expected_mode"),
    [
        (legacy.run_ui_legacy, ["--with-live", "--ticker", "qqq"], AppMode.UI_LIVE),
        (legacy.run_headless_live_legacy, ["--ticker", "qqq"], AppMode.HEADLESS_LIVE),
        (legacy.run_snapshot_legacy, ["--ticker", "qqq"], AppMode.SNAPSHOT_ONCE),
    ],
)
def test_legacy_shims_forward_into_unified_runtime(
    monkeypatch: pytest.MonkeyPatch,
    runner,
    argv: list[str],
    expected_mode: AppMode,
) -> None:
    captured: dict[str, LaunchConfig] = {}

    def _fake_run(launch: LaunchConfig, *, app=None) -> int:  # noqa: ANN001
        captured["launch"] = launch
        return 23

    monkeypatch.setattr("flow_app.legacy.run_launch_config", _fake_run)

    result = runner(argv)

    assert result == 23
    assert captured["launch"].mode is expected_mode
    assert captured["launch"].ticker == "QQQ"


def test_build_runtime_summary_marks_snapshot_once_as_full_surface() -> None:
    from flow_core.config.models import PipelineConfig

    summary = build_runtime_summary(
        LaunchConfig(mode=AppMode.SNAPSHOT_ONCE, ticker="SPY", expiration="2026-06-19"),
        PipelineConfig(
            live_focus_labels=["0DTE", "1DTE", "EOW"],
            live_hot_poll_seconds=15,
            live_full_snapshot_poll_seconds=300,
        ),
        refresh_ms=25,
    )

    assert summary["expiration"] == "full_surface (all expiries)"
    assert summary["live_focus_labels"] == "0DTE,1DTE,EOW"
    assert summary["live_hot_poll_seconds"] == 15
    assert summary["live_full_snapshot_poll_seconds"] == 300


@pytest.mark.asyncio
async def test_snapshot_once_uses_full_surface_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    class _Service:
        async def capture_snapshot(self, symbol: str) -> pl.DataFrame:
            captured.append(symbol)
            return pl.DataFrame({"symbol": [symbol]})

    monkeypatch.setattr(
        "flow_app.runtime.load_runtime_settings",
        lambda launch: (None, None),
    )
    monkeypatch.setattr("flow_app.runtime._build_cache", lambda config: None)
    monkeypatch.setattr("flow_app.runtime.build_pipeline_service", lambda cache, config, provider_map: _Service())

    result = await _run_snapshot_once(LaunchConfig(mode=AppMode.SNAPSHOT_ONCE, ticker="SPY", expiration="2026-06-19"))

    assert result == 0
    assert captured == ["SPY"]

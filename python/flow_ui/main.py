from __future__ import annotations

import logging
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
import pyqtgraph as pg
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from flow_core.orchestration.cache import InMemoryQuoteCache
from flow_core.orchestration.state_store import SymbolSnapshot
from flow_core.quant import scan_arbitrage_violations
from flow_ui.state_bridge import UIStateBridge
from flow_ui.page_payload_cache import PagePayloadCache
from flow_ui.symbol_controls import SymbolExpirationControls, configure_expiration_combo
from flow_ui.update_coordinator import UpdateCoordinator
from flow_ui.viewmodels import (
    build_short_expiry_scanner_payload,
    build_calendar_payload,
    build_density_payload,
    build_price_error_payload,
    build_runtime_metrics_payload,
    build_surface_validation_payload,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UIState:
    symbol: str = "SPY"
    health: str = "idle"
    last_version: int = 0


@dataclass(slots=True)
class LineVisibilityControl:
    title: str
    widget: QWidget
    label: QLabel
    grid_host: QWidget
    grid_layout: QGridLayout
    checkboxes: dict[str, QCheckBox] = field(default_factory=dict)
    hidden_keys: set[str] = field(default_factory=set)


ROUTED_GREEKS_COLUMN_HELP: dict[str, str] = {
    "price": "Model price retained for backward compatibility.",
    "model_price": "Price produced by the routed pricing engine.",
    "display_price": "Price displayed in the UI. Currently set to model_price.",
    "display_price_source": "Explains where the displayed price came from.",
    "market_bid": "Provider bid quote for the contract.",
    "market_ask": "Provider ask quote for the contract.",
    "market_last": "Provider last trade quote when available.",
    "market_mid": "Midpoint of bid/ask or last when the spread is unavailable.",
    "delta": "First derivative of model price with respect to spot.",
    "gamma": "Second derivative of model price with respect to spot.",
    "theta": "One-calendar-day forward difference, reported per day.",
    "vega": "Sensitivity of model price to implied volatility.",
    "rho": "Sensitivity of model price to rates.",
    "rate_used": "Risk-free rate fed into the routed engine.",
    "dividend_used": "Total projected discrete dividend amount used on the pricing path.",
    "tau_years": "Time to expiry in years used in pricing.",
    "engine_used": "Concrete implementation used to produce the row.",
    "greeks_engine": "Routed tenor bucket selected for Greeks.",
    "vega_method": "Computation method used for vega.",
    "rho_method": "Computation method used for rho.",
    "input_snapshot_kind": "Batch type that fed this row, such as live_batch or eod_final.",
    "batch_id": "Coherent batch identifier for row-level traceability.",
}


class PolarsTableModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self._rows: list[list[object]] = []
        self._columns: list[str] = []
        self._column_help: dict[str, str] = {}

    def update(self, rows: list[list[object]], columns: list[str], column_help: dict[str, str] | None = None) -> None:
        self.beginResetModel()
        self._rows = rows
        self._columns = columns
        self._column_help = dict(column_help or {})
        self.endResetModel()

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return len(self._rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):  # noqa: ANN201
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        return str(self._rows[index.row()][index.column()])

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # noqa: ANN201,N802
        if orientation == Qt.Horizontal:
            label = self._columns[section] if section < len(self._columns) else ""
            if role == Qt.DisplayRole:
                return label
            if role == Qt.ToolTipRole:
                return self._column_help.get(label, label)
            return None
        if role == Qt.DisplayRole:
            return str(section)
        return None


class MainWindow(QMainWindow):
    def __init__(
        self,
        cache: InMemoryQuoteCache,
        refresh_ms: int = 50,
        symbol: str = "SPY",
        bridge: UIStateBridge | None = None,
        default_space_mode: str = "residual",
        dual_mode_enabled: bool = False,
        ui_apply_p95_limit_ms: float = 50.0,
        ui_auto_degrade: bool = True,
        refresh_callback: Callable[[], str] | None = None,
        history_callback: Callable[[str, str], pl.DataFrame] | None = None,
        snapshot_timezone: str = "America/New_York",
        market_close_freeze_time: str = "17:00",
        final_prices_refresh_time: str = "17:30",
        oi_refresh_time: str = "20:30",
        session_config: dict[str, Any] | None = None,
        runtime_summary: dict[str, Any] | None = None,
        session_save_callback: Callable[[dict[str, Any]], str] | None = None,
        bootstrap_message: str | None = None,
        symbol_search_callback: Callable[[str], list[dict[str, str]]] | None = None,
        expiration_lookup_callback: Callable[[str], list[str]] | None = None,
        live_expiration: str | None = None,
        live_expiration_setter: Callable[[str | None], None] | None = None,
        live_expiration_enabled: bool = False,
        live_runtime_status_callback: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        super().__init__()
        self._cache = cache
        self._state = UIState(symbol=symbol)
        self._bridge = bridge or UIStateBridge(max_pending_per_symbol=1)
        self._coordinator = UpdateCoordinator()
        self._page_payload_cache = PagePayloadCache(max_entries=96)
        self._refresh_callback = refresh_callback
        self._history_callback = history_callback
        self._dirty_symbols: set[str] = set()
        self._line_items: dict[str, pg.PlotDataItem] = {}
        self._apply_latency_ms: deque[float] = deque(maxlen=240)
        self._ui_apply_p95_limit_ms = float(ui_apply_p95_limit_ms)
        self._ui_auto_degrade = bool(ui_auto_degrade)
        self._dual_mode_enabled = bool(dual_mode_enabled)
        self._snapshot_timezone = snapshot_timezone
        self._market_close_freeze_time = market_close_freeze_time
        self._final_prices_refresh_time = final_prices_refresh_time
        self._oi_refresh_time = oi_refresh_time
        self._time_series_frame = None
        self._session_config = dict(session_config or {})
        self._runtime_summary = dict(runtime_summary or {})
        self._session_save_callback = session_save_callback
        self._bootstrap_message = (bootstrap_message or "").strip()
        self._symbol_search_callback = symbol_search_callback
        self._expiration_lookup_callback = expiration_lookup_callback
        self._live_expiration_setter = live_expiration_setter
        self._live_expiration_enabled = bool(live_expiration_enabled)
        self._live_runtime_status_callback = live_runtime_status_callback
        self._scanner_selected_focus_label = "0DTE"

        self._cache.set_update_callback(self._bridge.coalesce)
        self._bridge.snapshot_ready.connect(self._on_snapshot_ready)
        self._coordinator.overlay_ready.connect(self._on_overlay_ready)

        self.setWindowTitle("Quant Pipeline MVP")
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        initial_live_text = (
            "Live monitor: waiting for first live batch" if self._live_expiration_enabled else "Live monitor: waiting for data"
        )
        if self._bootstrap_message == "no stored snapshot found" and self._live_expiration_enabled:
            initial_snapshot_text = "Snapshot status: no stored snapshot found yet; waiting for the first live batch"
        elif self._bootstrap_message:
            initial_snapshot_text = f"Snapshot status: {self._bootstrap_message}"
        else:
            initial_snapshot_text = "Snapshot status: waiting for data"
        self._live_label = QLabel(initial_live_text)
        self._snapshot_status = QLabel(initial_snapshot_text)
        self._live_expiration = QComboBox()
        self._live_expiration.currentIndexChanged.connect(self._on_live_expiration_changed)
        self._live_expiration_status = QLabel(
            "Live expiry: choose one expiry or leave Auto to follow the configured live scope."
        )
        self._live_runtime_status = QLabel("Live runtime: waiting for the first status update")
        self._refresh_button = QPushButton("Refresh Latest Snapshot")
        self._refresh_button.setText(self._refresh_button_text())
        self._refresh_button.clicked.connect(self._refresh_latest_snapshot)
        live_page = QWidget()
        live_layout = QVBoxLayout(live_page)
        live_controls = QHBoxLayout()
        live_controls.addWidget(QLabel("Live Expiry"))
        live_controls.addWidget(self._live_expiration)
        live_controls.addStretch(1)
        live_layout.addLayout(live_controls)
        live_layout.addWidget(self._live_expiration_status)
        live_layout.addWidget(self._live_runtime_status)
        live_layout.addWidget(self._live_label)
        live_layout.addWidget(self._snapshot_status)
        live_layout.addWidget(self._refresh_button)
        self._configure_live_expiration_controls(symbol, live_expiration)

        self._scanner_status = QLabel("SPY Short Expiry Scanner: waiting for focused expiry diagnostics")
        self._scanner_runtime_badge = QLabel("Scanner runtime: waiting for first batch")
        self._scanner_explain = QLabel(
            "Scanner is the landing page for SPY 0DTE, 1DTE, and EOW. Click an expiry card to sync the drilldown tabs "
            "without changing the live polling selection."
        )
        self._scanner_explain.setWordWrap(True)
        self._scanner_focus_buttons: dict[str, QPushButton] = {}
        scanner_cards = QHBoxLayout()
        for focus_label in ("0DTE", "1DTE", "EOW"):
            button = QPushButton(f"{focus_label}\nWaiting for data")
            button.setCheckable(True)
            button.setMinimumHeight(88)
            button.clicked.connect(
                lambda _checked, label=focus_label: self._on_scanner_focus_card_clicked(label)
            )
            self._scanner_focus_buttons[focus_label] = button
            scanner_cards.addWidget(button)
        scanner_cards.addStretch(1)
        self._scanner_heat_plot = pg.PlotWidget()
        self._scanner_heat_plot.setBackground("w")
        self._scanner_heat_plot.setLabel("bottom", "Strike")
        self._scanner_heat_plot.setLabel("left", "Focus Expiry")
        self._scanner_heat_img = pg.ImageItem()
        self._scanner_heat_plot.addItem(self._scanner_heat_img)
        self._scanner_color_bar = pg.ColorBarItem(label="Gamma OI Exposure", colorMap="CET-D1A")
        self._scanner_color_bar.setImageItem(self._scanner_heat_img)
        self._scanner_summary_model = PolarsTableModel()
        scanner_summary_table = QTableView()
        scanner_summary_table.setModel(self._scanner_summary_model)
        self._scanner_levels_model = PolarsTableModel()
        scanner_levels_table = QTableView()
        scanner_levels_table.setModel(self._scanner_levels_model)
        self._scanner_flow_model = PolarsTableModel()
        scanner_flow_table = QTableView()
        scanner_flow_table.setModel(self._scanner_flow_model)
        scanner_page = QWidget()
        scanner_layout = QVBoxLayout(scanner_page)
        scanner_layout.addWidget(self._scanner_status)
        scanner_layout.addWidget(self._scanner_runtime_badge)
        scanner_layout.addWidget(self._scanner_explain)
        scanner_layout.addLayout(scanner_cards)
        scanner_layout.addWidget(self._scanner_heat_plot)
        scanner_layout.addWidget(QLabel("Focused Expiry Summary"))
        scanner_layout.addWidget(scanner_summary_table)
        scanner_layout.addWidget(QLabel("Scanner Levels"))
        scanner_layout.addWidget(scanner_levels_table)
        scanner_layout.addWidget(QLabel("Flow Proxies"))
        scanner_layout.addWidget(scanner_flow_table)

        self._run_mode = QComboBox()
        self._run_mode.addItems(["ui_review", "ui_live", "headless_live", "snapshot_once"])
        self._run_mode.setCurrentText(str(self._session_config.get("mode", "ui_review")))
        self._run_symbol_controls = SymbolExpirationControls(
            ticker=str(self._session_config.get("ticker", symbol)),
            expiration=str(self._session_config.get("expiration", "") or "") or None,
            search_callback=self._symbol_search_callback,
            expiration_lookup_callback=self._expiration_lookup_callback,
        )
        self._run_mode.currentTextChanged.connect(self._run_symbol_controls.set_mode)
        self._run_refresh_ms = QSpinBox()
        self._run_refresh_ms.setRange(10, 60_000)
        self._run_refresh_ms.setSingleStep(25)
        self._run_refresh_ms.setValue(int(self._session_config.get("refresh_ms", refresh_ms) or refresh_ms))
        self._run_allow_shared = QCheckBox("Allow shared stream lock")
        self._run_allow_shared.setChecked(bool(self._session_config.get("allow_shared", False)))
        self._run_provider_config = QLineEdit(str(self._session_config.get("provider_config", "")))
        self._run_pipeline_config = QLineEdit(str(self._session_config.get("pipeline_config", "")))
        self._run_config_status = QLabel(
            "Run Config: topology changes are saved for the next launch. Refresh interval can be applied now."
        )
        self._run_runtime_summary = QLabel(self._runtime_summary_text())
        self._run_runtime_summary.setWordWrap(True)
        run_note = QLabel(
            "This panel replaces IDE-only launch customization. Use it to inspect the active runtime contract, "
            "hot-apply the UI refresh interval, and save the next startup session without editing repo defaults."
        )
        run_note.setWordWrap(True)
        run_form = QFormLayout()
        run_form.addRow("Next-Launch Mode", self._run_mode)
        run_form.addRow("Next-Launch Ticker", self._run_symbol_controls.ticker_input)
        run_form.addRow("Next-Launch Expiration", self._run_symbol_controls.expiration_combo)
        run_form.addRow("UI Refresh (ms)", self._run_refresh_ms)
        run_form.addRow("", self._run_allow_shared)
        run_form.addRow("Provider Config", self._run_provider_config)
        run_form.addRow("Pipeline Config", self._run_pipeline_config)
        self._run_symbol_controls.set_mode(self._run_mode.currentText())
        self._apply_display_button = QPushButton("Apply Refresh Now")
        self._apply_display_button.clicked.connect(self._apply_display_settings)
        self._save_session_button = QPushButton("Save Session For Next Launch")
        self._save_session_button.clicked.connect(self._save_session_settings)
        run_buttons = QHBoxLayout()
        run_buttons.addWidget(self._apply_display_button)
        run_buttons.addWidget(self._save_session_button)
        run_buttons.addStretch(1)
        run_page = QWidget()
        run_layout = QVBoxLayout(run_page)
        run_layout.addWidget(run_note)
        run_layout.addLayout(run_form)
        run_layout.addWidget(self._run_runtime_summary)
        run_layout.addWidget(self._run_config_status)
        run_layout.addLayout(run_buttons)

        self._iv_label = QLabel("IV/term structure: no samples yet")
        self._ssvi_model = PolarsTableModel()
        ssvi_table = QTableView()
        ssvi_table.setModel(self._ssvi_model)
        iv_page = QWidget()
        iv_layout = QVBoxLayout(iv_page)
        iv_layout.addWidget(self._iv_label)
        iv_layout.addWidget(ssvi_table)

        self._arb_model = PolarsTableModel()
        arb_table = QTableView()
        arb_table.setModel(self._arb_model)
        arb_page = QWidget()
        arb_layout = QVBoxLayout(arb_page)
        arb_layout.addWidget(arb_table)

        self._greeks_label = QLabel("Routed Greeks: no rows yet")
        self._greeks_detail = QLabel("Select a routed Greeks row to inspect inputs and provenance.")
        self._greeks_model = PolarsTableModel()
        greeks_table = QTableView()
        greeks_table.setModel(self._greeks_model)
        greeks_table.clicked.connect(self._on_greeks_row_selected)
        greeks_page = QWidget()
        greeks_layout = QVBoxLayout(greeks_page)
        greeks_layout.addWidget(self._greeks_label)
        greeks_layout.addWidget(greeks_table)
        greeks_layout.addWidget(self._greeks_detail)

        self._overlay_status = QLabel("Overlay: waiting for routed Greeks")
        self._overlay_greek = QComboBox()
        self._overlay_greek.addItems(["delta", "gamma", "theta", "vega", "rho", "price"])
        self._overlay_opt_type = QComboBox()
        self._overlay_opt_type.addItems(["all", "call", "put"])
        self._overlay_opt_type.setCurrentText("call")
        self._overlay_space = QComboBox()
        self._overlay_space.addItems(["log", "strike", "residual"])
        idx = self._overlay_space.findText(default_space_mode.lower())
        self._overlay_space.setCurrentIndex(idx if idx >= 0 else 2)
        self._overlay_expiry = QComboBox()
        self._overlay_expiry.addItem("all")
        self._engine_toggles: dict[str, QCheckBox] = {}
        for engine in ["fdm", "tree", "bs2002", "rim", "laplace"]:
            cb = QCheckBox(engine.upper())
            cb.setChecked(True)
            cb.stateChanged.connect(self._request_overlay_refresh)
            self._engine_toggles[engine] = cb
        self._overlay_dual_mode = QCheckBox("Dual Heatmap (Debug)")
        self._overlay_dual_mode.setChecked(False)
        self._overlay_dual_mode.setEnabled(self._dual_mode_enabled)
        self._overlay_dual_mode.stateChanged.connect(self._request_overlay_refresh)
        self._overlay_greek.currentIndexChanged.connect(self._request_overlay_refresh)
        self._overlay_opt_type.currentIndexChanged.connect(self._request_overlay_refresh)
        self._overlay_expiry.currentIndexChanged.connect(self._request_overlay_refresh)
        self._overlay_space.currentIndexChanged.connect(self._request_overlay_refresh)

        overlay_controls = QHBoxLayout()
        overlay_controls.addWidget(QLabel("Greek"))
        overlay_controls.addWidget(self._overlay_greek)
        overlay_controls.addWidget(QLabel("Option Type"))
        overlay_controls.addWidget(self._overlay_opt_type)
        overlay_controls.addWidget(QLabel("Space"))
        overlay_controls.addWidget(self._overlay_space)
        overlay_controls.addWidget(QLabel("Expiry"))
        overlay_controls.addWidget(self._overlay_expiry)
        overlay_controls.addWidget(self._overlay_dual_mode)
        for cb in self._engine_toggles.values():
            overlay_controls.addWidget(cb)
        overlay_controls.addStretch(1)

        overlay_page = QWidget()
        overlay_layout = QVBoxLayout(overlay_page)
        overlay_layout.addLayout(overlay_controls)
        overlay_layout.addWidget(self._overlay_status)
        self._overlay_explain = QLabel(
            "Overlay source: routed_greeks. Line plot shows the selected Greek against strike/log-moneyness; heatmap shows the same measure across strike and days-to-expiry."
        )
        self._overlay_explain.setWordWrap(True)
        overlay_layout.addWidget(self._overlay_explain)

        self._overlay_line_plot = pg.PlotWidget()
        self._overlay_line_plot.setBackground("w")
        self._overlay_line_plot.showGrid(x=True, y=True, alpha=0.2)
        self._overlay_line_plot.addLegend(offset=(10, 10))
        self._overlay_line_plot.setLabel("bottom", "Strike")
        self._overlay_line_plot.setLabel("left", "Greek")
        overlay_layout.addWidget(self._overlay_line_plot)
        self._overlay_line_visibility = self._make_line_visibility_control("Visible Overlay Lines")
        overlay_layout.addWidget(self._overlay_line_visibility.widget)

        self._overlay_heat_plot = pg.PlotWidget()
        self._overlay_heat_plot.setBackground("w")
        self._overlay_heat_plot.setLabel("bottom", "Strike")
        self._overlay_heat_plot.setLabel("left", "Days To Expiry")
        self._overlay_heat_img = pg.ImageItem()
        self._overlay_heat_plot.addItem(self._overlay_heat_img)
        self._overlay_color_bar = pg.ColorBarItem(label="Greek", colorMap="CET-L4")
        self._overlay_color_bar.setImageItem(self._overlay_heat_img)
        overlay_layout.addWidget(self._overlay_heat_plot)
        self._overlay_heat_plot_2 = pg.PlotWidget()
        self._overlay_heat_plot_2.setBackground("w")
        self._overlay_heat_plot_2.setLabel("bottom", "Alt Space")
        self._overlay_heat_plot_2.setLabel("left", "Days To Expiry")
        self._overlay_heat_img_2 = pg.ImageItem()
        self._overlay_heat_plot_2.addItem(self._overlay_heat_img_2)
        self._overlay_heat_plot_2.setVisible(False)
        overlay_layout.addWidget(self._overlay_heat_plot_2)

        self._routing_label = QLabel("Routing: no diagnostics yet")
        self._dispatch_model = PolarsTableModel()
        dispatch_table = QTableView()
        dispatch_table.setModel(self._dispatch_model)
        self._parity_model = PolarsTableModel()
        parity_table = QTableView()
        parity_table.setModel(self._parity_model)
        self._parity_detail_model = PolarsTableModel()
        parity_detail_table = QTableView()
        parity_detail_table.setModel(self._parity_detail_model)
        diag_page = QWidget()
        diag_layout = QVBoxLayout(diag_page)
        diag_layout.addWidget(self._routing_label)
        diag_layout.addWidget(dispatch_table)
        diag_layout.addWidget(parity_table)
        diag_layout.addWidget(parity_detail_table)

        self._temporal_status = QLabel("Temporal Greeks: waiting for routed Greeks history")
        self._temporal_explain = QLabel(
            "Temporal source: routed_greeks history from cache plus persisted parquet history. X=strike, Y=selected Greek, slider=batch timestamp for one expiry."
        )
        self._temporal_explain.setWordWrap(True)
        self._temporal_expiry = QComboBox()
        self._temporal_expiry.currentIndexChanged.connect(self._refresh_temporal_plot)
        self._temporal_greek = QComboBox()
        self._temporal_greek.addItems(["delta", "gamma", "theta", "vega", "rho", "model_price"])
        self._temporal_greek.currentIndexChanged.connect(self._refresh_temporal_plot)
        self._temporal_time_label = QLabel("Time: n/a")
        self._temporal_slider = QSlider(Qt.Horizontal)
        self._temporal_slider.setMinimum(0)
        self._temporal_slider.setMaximum(0)
        self._temporal_slider.valueChanged.connect(self._refresh_temporal_plot)
        self._temporal_plot = pg.PlotWidget()
        self._temporal_plot.setBackground("w")
        self._temporal_plot.setLabel("bottom", "Strike")
        self._temporal_plot.setLabel("left", "Greek")
        self._temporal_line = self._temporal_plot.plot([], [], pen=pg.mkPen(QColor("#1f77b4"), width=2))
        temporal_controls = QHBoxLayout()
        temporal_controls.addWidget(QLabel("Expiry"))
        temporal_controls.addWidget(self._temporal_expiry)
        temporal_controls.addWidget(QLabel("Greek"))
        temporal_controls.addWidget(self._temporal_greek)
        temporal_page = QWidget()
        temporal_layout = QVBoxLayout(temporal_page)
        temporal_layout.addLayout(temporal_controls)
        temporal_layout.addWidget(self._temporal_status)
        temporal_layout.addWidget(self._temporal_explain)
        temporal_layout.addWidget(self._temporal_time_label)
        temporal_layout.addWidget(self._temporal_slider)
        temporal_layout.addWidget(self._temporal_plot)

        self._price_error_status = QLabel("Model vs Market: waiting for routed Greeks")
        self._price_error_explain = QLabel(
            "Source: routed_greeks. Upper plot is model_price and market_mid against strike for one expiry. Lower plot is model-minus-market price error against strike."
        )
        self._price_error_explain.setWordWrap(True)
        self._price_error_expiry = QComboBox()
        self._price_error_expiry.currentIndexChanged.connect(self._refresh_price_error_plot)
        self._price_error_mode = QComboBox()
        self._price_error_mode.addItems(["absolute", "relative"])
        self._price_error_mode.currentIndexChanged.connect(self._refresh_price_error_plot)
        self._price_error_option = QComboBox()
        self._price_error_option.addItems(["call", "put", "all"])
        self._price_error_option.currentIndexChanged.connect(self._refresh_price_error_plot)
        self._price_error_plot = pg.PlotWidget()
        self._price_error_plot.setBackground("w")
        self._price_error_plot.setLabel("bottom", "Strike")
        self._price_error_plot.setLabel("left", "Price")
        self._price_error_plot.addLegend(offset=(10, 10))
        self._price_error_line_items: dict[str, pg.PlotDataItem] = {}
        self._price_error_line_visibility = self._make_line_visibility_control("Visible Price Lines")
        self._price_error_delta_plot = pg.PlotWidget()
        self._price_error_delta_plot.setBackground("w")
        self._price_error_delta_plot.setLabel("bottom", "Strike")
        self._price_error_delta_plot.setLabel("left", "Error")
        self._price_error_delta_plot.addLegend(offset=(10, 10))
        self._price_error_delta_items: dict[str, pg.PlotDataItem] = {}
        self._price_error_delta_visibility = self._make_line_visibility_control("Visible Error Lines")
        price_controls = QHBoxLayout()
        price_controls.addWidget(QLabel("Expiry"))
        price_controls.addWidget(self._price_error_expiry)
        price_controls.addWidget(QLabel("Option Type"))
        price_controls.addWidget(self._price_error_option)
        price_controls.addWidget(QLabel("Error Mode"))
        price_controls.addWidget(self._price_error_mode)
        price_controls.addStretch(1)
        price_page = QWidget()
        price_layout = QVBoxLayout(price_page)
        price_layout.addLayout(price_controls)
        price_layout.addWidget(self._price_error_status)
        price_layout.addWidget(self._price_error_explain)
        price_layout.addWidget(self._price_error_plot)
        price_layout.addWidget(self._price_error_line_visibility.widget)
        price_layout.addWidget(self._price_error_delta_plot)
        price_layout.addWidget(self._price_error_delta_visibility.widget)

        self._validation_status = QLabel("Validation Workspace: waiting for surface diagnostics")
        self._validation_explain = QLabel(
            "Slice Explorer and Surface Explorer use persisted surface diagnostics from the latest batch."
        )
        self._validation_explain.setWordWrap(True)
        self._validation_metric = QComboBox()
        self._validation_metric.addItems(
            [
                "implied_vol",
                "price",
                "iv_bid",
                "iv_ask",
                "iv_ref",
                "vendor_iv_ref",
                "american_model_price",
                "dual_delta_bid",
                "dual_delta_ask",
                "dual_delta_ref",
                "price_second_derivative_ref",
                "delta",
                "gamma",
                "theta",
                "vega",
                "rho",
                "vol_error_abs",
                "price_error_abs",
            ]
        )
        self._validation_metric.currentIndexChanged.connect(self._refresh_validation_view)
        self._validation_option = QComboBox()
        self._validation_option.addItems(["call", "put", "all"])
        self._validation_option.currentIndexChanged.connect(self._refresh_validation_view)
        self._validation_expiry = QComboBox()
        self._validation_expiry.currentIndexChanged.connect(self._refresh_validation_view)
        self._validation_line_plot = pg.PlotWidget()
        self._validation_line_plot.setBackground("w")
        self._validation_line_plot.setLabel("bottom", "Strike")
        self._validation_line_plot.setLabel("left", "Metric")
        self._validation_line_plot.addLegend(offset=(10, 10))
        self._validation_line_items: dict[str, pg.PlotDataItem] = {}
        self._validation_line_visibility = self._make_line_visibility_control("Visible Slice Lines")
        self._validation_heat_plot = pg.PlotWidget()
        self._validation_heat_plot.setBackground("w")
        self._validation_heat_plot.setLabel("bottom", "Strike")
        self._validation_heat_plot.setLabel("left", "Days To Expiry")
        self._validation_heat_img = pg.ImageItem()
        self._validation_heat_plot.addItem(self._validation_heat_img)
        self._validation_color_bar = pg.ColorBarItem(label="Validation Metric", colorMap="CET-L4")
        self._validation_color_bar.setImageItem(self._validation_heat_img)
        self._surface_summary_model = PolarsTableModel()
        surface_summary_table = QTableView()
        surface_summary_table.setModel(self._surface_summary_model)
        validation_controls = QHBoxLayout()
        validation_controls.addWidget(QLabel("Metric"))
        validation_controls.addWidget(self._validation_metric)
        validation_controls.addWidget(QLabel("Option Type"))
        validation_controls.addWidget(self._validation_option)
        validation_controls.addWidget(QLabel("Expiry"))
        validation_controls.addWidget(self._validation_expiry)
        validation_controls.addStretch(1)
        validation_page = QWidget()
        validation_layout = QVBoxLayout(validation_page)
        validation_layout.addLayout(validation_controls)
        validation_layout.addWidget(self._validation_status)
        validation_layout.addWidget(self._validation_explain)
        validation_layout.addWidget(self._validation_line_plot)
        validation_layout.addWidget(self._validation_line_visibility.widget)
        validation_layout.addWidget(self._validation_heat_plot)
        validation_layout.addWidget(surface_summary_table)

        self._calendar_status = QLabel("Calendar / Density: waiting for surface diagnostics")
        self._calendar_explain = QLabel(
            "Calendar Inspector shows total variance across strike and expiry; density uses latest-batch model prices."
        )
        self._calendar_explain.setWordWrap(True)
        self._calendar_option = QComboBox()
        self._calendar_option.addItems(["call", "put", "all"])
        self._calendar_option.currentIndexChanged.connect(self._refresh_calendar_density_view)
        self._density_expiry = QComboBox()
        self._density_expiry.currentIndexChanged.connect(self._refresh_calendar_density_view)
        calendar_controls = QHBoxLayout()
        calendar_controls.addWidget(QLabel("Option Type"))
        calendar_controls.addWidget(self._calendar_option)
        calendar_controls.addWidget(QLabel("Density Expiry"))
        calendar_controls.addWidget(self._density_expiry)
        calendar_controls.addStretch(1)
        self._calendar_heat_plot = pg.PlotWidget()
        self._calendar_heat_plot.setBackground("w")
        self._calendar_heat_plot.setLabel("bottom", "Strike")
        self._calendar_heat_plot.setLabel("left", "Days To Expiry")
        self._calendar_heat_img = pg.ImageItem()
        self._calendar_heat_plot.addItem(self._calendar_heat_img)
        self._calendar_color_bar = pg.ColorBarItem(label="Total Variance", colorMap="CET-L4")
        self._calendar_color_bar.setImageItem(self._calendar_heat_img)
        self._density_plot = pg.PlotWidget()
        self._density_plot.setBackground("w")
        self._density_plot.setLabel("bottom", "Strike")
        self._density_plot.setLabel("left", "Density")
        self._density_plot.addLegend(offset=(10, 10))
        self._density_items: dict[str, pg.PlotDataItem] = {}
        self._density_visibility = self._make_line_visibility_control("Visible Density Lines")
        self._calendar_violation_model = PolarsTableModel()
        calendar_violation_table = QTableView()
        calendar_violation_table.setModel(self._calendar_violation_model)
        calendar_page = QWidget()
        calendar_layout = QVBoxLayout(calendar_page)
        calendar_layout.addLayout(calendar_controls)
        calendar_layout.addWidget(self._calendar_status)
        calendar_layout.addWidget(self._calendar_explain)
        calendar_layout.addWidget(self._calendar_heat_plot)
        calendar_layout.addWidget(self._density_plot)
        calendar_layout.addWidget(self._density_visibility.widget)
        calendar_layout.addWidget(calendar_violation_table)

        self._runtime_metrics_status = QLabel("Runtime Metrics: waiting for latency samples")
        self._runtime_metrics_explain = QLabel(
            "Runtime metrics show stage-level latency trends and the latest persisted batch metrics."
        )
        self._runtime_metrics_explain.setWordWrap(True)
        self._runtime_metrics_plot = pg.PlotWidget()
        self._runtime_metrics_plot.setBackground("w")
        self._runtime_metrics_plot.setLabel("bottom", "Recent Batch Index")
        self._runtime_metrics_plot.setLabel("left", "Latency (ms)")
        self._runtime_metrics_plot.addLegend(offset=(10, 10))
        self._runtime_metric_items: dict[str, pg.PlotDataItem] = {}
        self._runtime_metric_visibility = self._make_line_visibility_control("Visible Metric Lines")
        self._runtime_metrics_model = PolarsTableModel()
        runtime_metrics_table = QTableView()
        runtime_metrics_table.setModel(self._runtime_metrics_model)
        runtime_page = QWidget()
        runtime_layout = QVBoxLayout(runtime_page)
        runtime_layout.addWidget(self._runtime_metrics_status)
        runtime_layout.addWidget(self._runtime_metrics_explain)
        runtime_layout.addWidget(self._runtime_metrics_plot)
        runtime_layout.addWidget(self._runtime_metric_visibility.widget)
        runtime_layout.addWidget(runtime_metrics_table)

        tabs.addTab(scanner_page, "Short Expiry Scanner")
        tabs.addTab(run_page, "Run Config")
        tabs.addTab(live_page, "Live Chain")
        tabs.addTab(iv_page, "SSVI vs Baseline")
        tabs.addTab(greeks_page, "Routed Greeks")
        tabs.addTab(overlay_page, "Greeks Overlay")
        tabs.addTab(price_page, "Model vs Market")
        tabs.addTab(validation_page, "Validation Workspace")
        tabs.addTab(calendar_page, "Calendar / Density")
        tabs.addTab(runtime_page, "Runtime Metrics")
        tabs.addTab(temporal_page, "Temporal Greeks")
        tabs.addTab(arb_page, "Arbitrage Scanner")
        tabs.addTab(diag_page, "Routing & Parity")

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._apply_pending_updates)
        self._timer.start(max(refresh_ms, 10))
        existing = self._cache.get_snapshot_nowait(symbol)
        if existing is not None:
            self._dirty_symbols.add(symbol)
            self._bridge.coalesce(symbol, existing.version)

    def closeEvent(self, event):  # noqa: ANN001,N802
        self._coordinator.shutdown()
        super().closeEvent(event)

    def _on_snapshot_ready(self, symbol: str, version: int) -> None:
        if symbol != self._state.symbol:
            return
        self._dirty_symbols.add(symbol)
        self._state.last_version = max(self._state.last_version, version)

    def _apply_pending_updates(self) -> None:
        started = time.perf_counter()
        self._refresh_button.setText(self._refresh_button_text())
        self._refresh_live_runtime_status()
        if self._state.symbol not in self._dirty_symbols:
            return
        version = self._bridge.consume_latest(self._state.symbol)
        self._dirty_symbols.discard(self._state.symbol)
        if version is None:
            return
        snapshot = self._cache.get_snapshot_nowait(self._state.symbol)
        if snapshot is None:
            return
        if snapshot.version < version:
            # A newer snapshot is expected; wait for next signal.
            self._dirty_symbols.add(self._state.symbol)
            return
        self._apply_snapshot(snapshot)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._apply_latency_ms.append(elapsed_ms)
        if self._ui_auto_degrade and len(self._apply_latency_ms) >= 20 and self._overlay_dual_mode.isChecked():
            p95 = float(np.quantile(np.asarray(self._apply_latency_ms, dtype=np.float64), 0.95))
            if p95 > self._ui_apply_p95_limit_ms:
                self._overlay_dual_mode.setChecked(False)
                logger.warning(
                    "ui_auto_degrade activated p95=%.2fms > limit=%.2fms",
                    p95,
                    self._ui_apply_p95_limit_ms,
                )

    def _apply_snapshot(self, snapshot: SymbolSnapshot) -> None:
        frame = snapshot.raw
        self._page_payload_cache.bind_batch(snapshot.batch_id)
        if frame.is_empty():
            self._page_payload_cache.clear()
            self._live_label.setText("Live monitor: no rows in cache")
            self._snapshot_status.setText("Snapshot status: no cached snapshot")
            self._iv_label.setText("IV/term structure: no rows in cache")
            self._ssvi_model.update([["No SSVI diagnostics"]], ["status"])
            self._greeks_label.setText("Routed Greeks: no rows in cache")
            self._greeks_model.update([["No routed Greeks"]], ["status"], ROUTED_GREEKS_COLUMN_HELP)
            self._greeks_detail.setText("Select a routed Greeks row to inspect inputs and provenance.")
            self._overlay_status.setText("Overlay: no routed Greeks in cache")
            self._arb_model.update([], ["status"])
            self._dispatch_model.update([["No routing summary"]], ["status"])
            self._parity_model.update([["No parity diagnostics"]], ["status"])
            self._parity_detail_model.update([["No parity detail diagnostics"]], ["status"])
            self._routing_label.setText("Routing: no diagnostics yet")
            self._temporal_status.setText("Temporal Greeks: no routed Greeks history")
            self._price_error_status.setText("Model vs Market: no routed Greeks in cache")
            self._validation_status.setText("Validation Workspace: no surface diagnostics in cache")
            self._calendar_status.setText("Calendar / Density: no surface diagnostics in cache")
            self._runtime_metrics_status.setText("Runtime Metrics: no metrics in cache")
            self._scanner_status.setText("SPY Short Expiry Scanner: no focused expiry diagnostics in cache")
            self._scanner_runtime_badge.setText("Scanner runtime: waiting for first batch")
            self._scanner_summary_model.update([["No scanner summary"]], ["status"])
            self._scanner_levels_model.update([["No scanner levels"]], ["status"])
            self._scanner_flow_model.update([["No proxy flow diagnostics"]], ["status"])
            self._sync_scanner_focus_cards(pl.DataFrame(), self._scanner_selected_focus_label)
            self._scanner_heat_img.setImage(np.ascontiguousarray(np.zeros((1, 1), dtype=np.float32)), autoLevels=False)
            self._scanner_heat_img.setRect(QRectF(0.0, 0.0, 1.0, 1.0))
            self._scanner_heat_img.setLevels((0.0, 1.0))
            self._scanner_color_bar.setLevels((0.0, 1.0))
            return

        self._live_label.setText(
            f"Live monitor: symbol={snapshot.symbol} rows={frame.height} v={snapshot.version} batch={snapshot.batch_id}"
        )
        notice = self._schedule_notice(snapshot)
        suffix = f" notice={notice}" if notice else ""
        self._refresh_button.setText(self._refresh_button_text())
        self._snapshot_status.setText(
            f"Snapshot status: kind={snapshot.snapshot_kind} final={snapshot.is_final_for_day} trading_date={snapshot.trading_date}{suffix}"
        )

        ssvi = snapshot.ssvi
        if not ssvi.is_empty():
            row = ssvi.to_dicts()[0]
            fit_space = str(row.get("fit_space", "log"))
            objective = float(row.get("objective", float("nan")))
            compare_space = str(row.get("compare_fit_space", ""))
            compare_objective = float(row.get("compare_objective", float("nan")))
            if compare_space:
                self._iv_label.setText(
                    f"SSVI objective: {fit_space}={objective:.6f} vs {compare_space}={compare_objective:.6f}"
                )
            else:
                self._iv_label.setText(f"SSVI objective: {fit_space}={objective:.6f}")
            show = ssvi.select(
                [
                    "fit_space",
                    "objective",
                    "iterations",
                    "success",
                    "compare_fit_space",
                    "compare_objective",
                    "compare_iterations",
                    "compare_success",
                ]
            )
            self._ssvi_model.update(show.rows(), show.columns)
        else:
            self._ssvi_model.update([["No SSVI diagnostics"]], ["status"])

        greeks = snapshot.greeks
        if greeks.is_empty():
            self._greeks_label.setText("Routed Greeks: no rows")
            self._greeks_model.update([["No routed Greeks"]], ["status"], ROUTED_GREEKS_COLUMN_HELP)
            self._greeks_detail.setText("Select a routed Greeks row to inspect inputs and provenance.")
            self._overlay_status.setText("Overlay: no routed Greeks")
        else:
            ok = int(greeks["success"].sum()) if "success" in greeks.columns else 0
            total = greeks.height
            self._greeks_label.setText(f"Routed Greeks: success={ok}/{total}")
            show_cols = [
                "expiration",
                "option_type",
                "strike",
                "greeks_engine",
                "engine_used",
                "market_mid",
                "model_price",
                "display_price_source",
                "rate_used",
                "dividend_used",
                "tau_years",
                "price",
                "delta",
                "gamma",
                "theta",
                "vega",
                "rho",
                "success",
            ]
            use_cols = [c for c in show_cols if c in greeks.columns]
            self._greeks_model.update(greeks.select(use_cols).head(120).rows(), use_cols, ROUTED_GREEKS_COLUMN_HELP)
            self._refresh_expiry_options(greeks)
            self._refresh_temporal_controls()
            self._coordinator.request_overlay(
                snapshot,
                greek=self._overlay_greek.currentText(),
                option_type=self._overlay_opt_type.currentText(),
                expiry_filter=self._overlay_expiry.currentText(),
                space_mode=self._overlay_space.currentText(),
                engine_mask=self._selected_engine_mask(),
                dual_mode=bool(self._overlay_dual_mode.isChecked()),
            )

        if all(c in frame.columns for c in ("bid", "ask", "strike", "option_type")):
            violations = scan_arbitrage_violations(frame)
            if violations.is_empty():
                self._arb_model.update([["No violations found"]], ["status"])
            else:
                show = violations.head(150)
                self._arb_model.update(show.rows(), show.columns)

        dispatch = snapshot.dispatch
        if dispatch.is_empty():
            self._dispatch_model.update([["No routing summary"]], ["status"])
            self._routing_label.setText("Routing: no dispatch summary yet")
        else:
            self._dispatch_model.update(dispatch.head(60).rows(), dispatch.columns)
            iv_engines = sorted(set(dispatch["iv_engine"].to_list()))
            greeks_engines = sorted(set(dispatch["greeks_engine"].to_list()))
            self._routing_label.setText(
                f"Routing engines: IV={', '.join(iv_engines)} | Greeks={', '.join(greeks_engines)}"
            )

        parity = snapshot.parity
        if parity.is_empty():
            self._parity_model.update([["No parity diagnostics"]], ["status"])
        else:
            show = parity.select(
                [
                    "expiration",
                    "winner_model",
                    "bjerksund_error",
                    "luba_error",
                    "winner_gap",
                    "pairs",
                    "tau_years",
                ]
            ).head(60)
            self._parity_model.update(show.rows(), show.columns)

        parity_detail = snapshot.parity_detail
        if parity_detail.is_empty():
            self._parity_detail_model.update([["No parity detail diagnostics"]], ["status"])
        else:
            show = (
                parity_detail.sort("parity_error", descending=True)
                .select(["expiration", "strike", "model", "parity_error", "relative_error"])
                .head(60)
            )
            self._parity_detail_model.update(show.rows(), show.columns)

        self._refresh_price_error_controls()
        self._refresh_price_error_plot()
        self._refresh_validation_controls()
        self._refresh_validation_view()
        self._refresh_calendar_controls()
        self._refresh_calendar_density_view()
        self._refresh_runtime_metrics_view()
        self._refresh_scanner_view()

    def _refresh_expiry_options(self, greeks) -> None:  # noqa: ANN001
        if "expiration" not in greeks.columns:
            return
        expiries = sorted({str(x) for x in greeks["expiration"].to_list() if x is not None})
        current = self._overlay_expiry.currentText() if self._overlay_expiry.count() > 0 else "all"
        self._overlay_expiry.blockSignals(True)
        self._overlay_expiry.clear()
        self._overlay_expiry.addItem("all")
        for exp in expiries:
            self._overlay_expiry.addItem(exp)
        idx = self._overlay_expiry.findText(current)
        self._overlay_expiry.setCurrentIndex(idx if idx >= 0 else 0)
        self._overlay_expiry.blockSignals(False)

    def _refresh_price_error_controls(self) -> None:
        frame = self._history_frame("surface_points")
        if frame.is_empty():
            snapshot = self._cache.get_snapshot_nowait(self._state.symbol)
            frame = snapshot.greeks if snapshot is not None else pl.DataFrame()
        if frame.is_empty() or "expiration" not in frame.columns:
            return
        latest = frame
        if "asof_ts" in latest.columns:
            latest_ts = latest["asof_ts"].max()
            latest = latest.filter(pl.col("asof_ts") == latest_ts)
        if "batch_id" in latest.columns and not latest.is_empty():
            latest_batch = str(latest["batch_id"][-1])
            latest = latest.filter(pl.col("batch_id").cast(pl.String) == latest_batch)
        expiries = sorted({str(x) for x in latest["expiration"].to_list() if x is not None})
        current = self._price_error_expiry.currentText() if self._price_error_expiry.count() > 0 else ""
        self._price_error_expiry.blockSignals(True)
        self._price_error_expiry.clear()
        for exp in expiries:
            self._price_error_expiry.addItem(exp)
        idx = self._price_error_expiry.findText(current)
        self._price_error_expiry.setCurrentIndex(idx if idx >= 0 else 0)
        self._price_error_expiry.blockSignals(False)

    def _refresh_validation_controls(self) -> None:
        frame = self._history_frame("surface_points")
        if frame.is_empty() or "expiration" not in frame.columns:
            self._validation_status.setText("Validation Workspace: no surface diagnostics history")
            return
        latest = frame.sort([c for c in ("asof_ts", "batch_id", "expiration") if c in frame.columns]).tail(frame.height)
        expiries = sorted({str(x) for x in latest["expiration"].to_list() if x is not None})
        current = self._validation_expiry.currentText() if self._validation_expiry.count() > 0 else "all"
        self._validation_expiry.blockSignals(True)
        self._validation_expiry.clear()
        self._validation_expiry.addItem("all")
        for exp in expiries:
            self._validation_expiry.addItem(exp)
        idx = self._validation_expiry.findText(current)
        self._validation_expiry.setCurrentIndex(idx if idx >= 0 else 0)
        self._validation_expiry.blockSignals(False)

    def _refresh_calendar_controls(self) -> None:
        frame = self._history_frame("surface_points")
        if frame.is_empty() or "expiration" not in frame.columns:
            self._calendar_status.setText("Calendar / Density: no surface diagnostics history")
            return
        expiries = sorted({str(x) for x in frame["expiration"].to_list() if x is not None})
        current = self._density_expiry.currentText() if self._density_expiry.count() > 0 else ""
        self._density_expiry.blockSignals(True)
        self._density_expiry.clear()
        for exp in expiries:
            self._density_expiry.addItem(exp)
        idx = self._density_expiry.findText(current)
        self._density_expiry.setCurrentIndex(idx if idx >= 0 else 0)
        self._density_expiry.blockSignals(False)

    def _history_frame(self, dataset: str) -> pl.DataFrame:
        history = self._cache.get_history_nowait(self._state.symbol, dataset)
        if self._history_callback is None:
            return history
        persisted = self._history_callback(self._state.symbol, dataset)
        if history.is_empty():
            return persisted
        if persisted.is_empty():
            return history
        merged = pl.concat([persisted, history], how="diagonal")
        unique_cols = [c for c in ("batch_id", "asof_ts", "expiration", "option_type", "strike", "engine_used") if c in merged.columns]
        if unique_cols:
            merged = merged.unique(subset=unique_cols, keep="last")
        sort_cols = [c for c in ("asof_ts", "expiration", "strike") if c in merged.columns]
        if sort_cols:
            merged = merged.sort(sort_cols)
        return merged

    def _payload_batch_id(self) -> str:
        snapshot = self._cache.get_snapshot_nowait(self._state.symbol)
        return snapshot.batch_id if snapshot is not None else ""

    def _cached_page_payload(
        self,
        *,
        page: str,
        key: tuple[object, ...],
        builder: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        return self._page_payload_cache.get_or_build(
            batch_id=self._payload_batch_id(),
            page=page,
            key=key,
            builder=builder,
        )

    def _make_line_visibility_control(self, title: str) -> LineVisibilityControl:
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(4)
        label = QLabel(title)
        label.setWordWrap(True)
        panel_layout.addWidget(label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setMaximumHeight(88)
        grid_host = QWidget()
        grid_layout = QGridLayout(grid_host)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setHorizontalSpacing(12)
        grid_layout.setVerticalSpacing(4)
        scroll.setWidget(grid_host)
        panel_layout.addWidget(scroll)
        panel.setVisible(False)
        return LineVisibilityControl(
            title=title,
            widget=panel,
            label=label,
            grid_host=grid_host,
            grid_layout=grid_layout,
        )

    def _on_line_visibility_changed(
        self,
        control: LineVisibilityControl,
        storage: dict[str, pg.PlotDataItem],
        series_key: str,
        state: int,
    ) -> None:
        visible = state == Qt.Checked
        if visible:
            control.hidden_keys.discard(series_key)
        else:
            control.hidden_keys.add(series_key)
        item = storage.get(series_key)
        if item is not None:
            item.setVisible(visible)

    def _sync_line_visibility_controls(
        self,
        control: LineVisibilityControl | None,
        storage: dict[str, pg.PlotDataItem],
        line_series: dict[str, object],
    ) -> None:
        if control is None:
            return

        incoming = set(line_series.keys())
        existing = set(control.checkboxes.keys())
        for key in sorted(existing - incoming):
            checkbox = control.checkboxes.pop(key)
            checkbox.deleteLater()

        for key in sorted(incoming):
            if key not in control.checkboxes:
                checkbox = QCheckBox(key)
                checkbox.setToolTip(key)
                checkbox.setChecked(key not in control.hidden_keys)
                checkbox.stateChanged.connect(
                    lambda state, series_key=key, ctl=control, store=storage: self._on_line_visibility_changed(
                        ctl,
                        store,
                        series_key,
                        state,
                    )
                )
                control.checkboxes[key] = checkbox

            checkbox = control.checkboxes[key]
            should_show = key not in control.hidden_keys
            if checkbox.isChecked() != should_show:
                checkbox.blockSignals(True)
                checkbox.setChecked(should_show)
                checkbox.blockSignals(False)
            item = storage.get(key)
            if item is not None:
                item.setVisible(should_show)

        while control.grid_layout.count():
            grid_item = control.grid_layout.takeAt(0)
            widget = grid_item.widget()
            if widget is not None:
                control.grid_layout.removeWidget(widget)

        for idx, key in enumerate(sorted(control.checkboxes)):
            row, col = divmod(idx, 2)
            control.grid_layout.addWidget(control.checkboxes[key], row, col)

        has_series = bool(control.checkboxes)
        control.label.setText(f"{control.title}: {len(control.checkboxes)} series")
        control.widget.setVisible(has_series)

    def _request_overlay_refresh(self) -> None:
        snapshot = self._cache.get_snapshot_nowait(self._state.symbol)
        if snapshot is None or snapshot.greeks.is_empty():
            return
        self._coordinator.request_overlay(
            snapshot,
            greek=self._overlay_greek.currentText(),
            option_type=self._overlay_opt_type.currentText(),
            expiry_filter=self._overlay_expiry.currentText(),
            space_mode=self._overlay_space.currentText(),
            engine_mask=self._selected_engine_mask(),
            dual_mode=bool(self._overlay_dual_mode.isChecked()),
        )

    def _on_overlay_ready(self, symbol: str, version: int, payload: dict) -> None:  # noqa: ANN401
        if symbol != self._state.symbol:
            return
        snapshot = self._cache.get_snapshot_nowait(symbol)
        if snapshot is None or snapshot.version != version:
            return
        self._cache.publish_overlay_payloads(symbol, version, {"overlay": payload})

        line_series = payload.get("line_series", {})
        heat = payload.get("heat_image")
        rect = payload.get("rect", (0.0, 0.0, 1.0, 1.0))
        levels = payload.get("levels", (0.0, 1.0))
        meta = payload.get("meta", {})

        x_label = "Log-Moneyness" if self._overlay_space.currentText() == "log" else "Strike"
        if self._overlay_space.currentText() == "residual":
            x_label = "Strike"
        self._overlay_line_plot.setLabel("bottom", x_label)
        self._overlay_heat_plot.setLabel("bottom", x_label)
        self._overlay_line_plot.setLabel("left", self._overlay_greek.currentText())
        self._update_line_plot(line_series)
        if heat is not None:
            self._overlay_heat_img.setImage(heat, autoLevels=False, autoDownsample=True)
            self._overlay_heat_img.setRect(QRectF(*rect))
            self._overlay_heat_img.setLevels(levels)
            self._overlay_color_bar.setLevels(levels)
            y_ticks = list(zip(meta.get("y_axis_values", []), meta.get("y_axis_labels", [])))
            if y_ticks:
                self._overlay_heat_plot.getAxis("left").setTicks([y_ticks])
        heat2 = payload.get("heat_image_secondary")
        rect2 = payload.get("rect_secondary")
        if heat2 is not None and rect2 is not None and self._overlay_dual_mode.isChecked():
            self._overlay_heat_plot_2.setVisible(True)
            self._overlay_heat_img_2.setImage(heat2, autoLevels=False, autoDownsample=True)
            self._overlay_heat_img_2.setRect(QRectF(*rect2))
            self._overlay_heat_img_2.setLevels(levels)
            y_ticks = list(zip(meta.get("y_axis_values", []), meta.get("y_axis_labels", [])))
            if y_ticks:
                self._overlay_heat_plot_2.getAxis("left").setTicks([y_ticks])
        else:
            self._overlay_heat_plot_2.setVisible(False)
        degenerate = " degenerate=single_expiry" if meta.get("is_single_expiry") else ""
        status = meta.get("status", "ok")
        self._overlay_explain.setText(
            f"{meta.get('chart_explanation', 'Overlay of routed Greeks.')}"
            f" Source: {meta.get('data_source', 'routed_greeks')}."
        )
        self._overlay_status.setText(
            f"Overlay: mode={self._overlay_space.currentText()} greek={self._overlay_greek.currentText()} rows={meta.get('rows', 0)} engine={meta.get('heat_engine', '')} status={status}{degenerate} v={version}"
        )

    def _update_line_plot(self, line_series: dict[str, object]) -> None:
        self._update_plot_series(
            self._overlay_line_plot,
            self._line_items,
            line_series,
            control=self._overlay_line_visibility,
        )

    def _update_plot_series(
        self,
        plot: pg.PlotWidget,
        storage: dict[str, pg.PlotDataItem],
        line_series: dict[str, object],
        *,
        control: LineVisibilityControl | None = None,
    ) -> None:
        existing = set(storage.keys())
        incoming = set(line_series.keys())
        for key in sorted(existing - incoming):
            item = storage.pop(key)
            plot.removeItem(item)

        palette = ["#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#17becf", "#8c564b", "#2f4f4f", "#3a86ff"]
        for idx, key in enumerate(sorted(incoming)):
            arr = line_series[key]
            if arr is None:
                continue
            if key not in storage:
                item = plot.plot(
                    [],
                    [],
                    pen=pg.mkPen(QColor(palette[idx % len(palette)]), width=2),
                    name=key,
                )
                storage[key] = item
            item = storage[key]
            item.setData(arr[:, 0], arr[:, 1])
        self._sync_line_visibility_controls(control, storage, line_series)

    def _refresh_price_error_plot(self) -> None:
        snapshot = self._cache.get_snapshot_nowait(self._state.symbol)
        surface_points = self._history_frame("surface_points")
        if (snapshot is None or snapshot.greeks.is_empty()) and surface_points.is_empty():
            self._price_error_status.setText("Model vs Market: no surface or routed-Greeks data available")
            return
        expiry = self._price_error_expiry.currentText() or "all"
        relative = self._price_error_mode.currentText() == "relative"
        engine_mask = self._selected_engine_mask()
        payload = self._cached_page_payload(
            page="price_error",
            key=(
                self._price_error_option.currentText(),
                expiry,
                relative,
                tuple(sorted(engine_mask)),
                "surface_points" if not surface_points.is_empty() else "snapshot",
            ),
            builder=lambda: build_price_error_payload(
                surface_points if not surface_points.is_empty() else snapshot,
                option_type=self._price_error_option.currentText(),
                expiry_filter=expiry,
                engine_mask=engine_mask,
                relative=relative,
            ),
        )
        meta = payload.get("meta", {})
        self._update_plot_series(
            self._price_error_plot,
            self._price_error_line_items,
            payload.get("line_series", {}),
            control=self._price_error_line_visibility,
        )
        self._update_plot_series(
            self._price_error_delta_plot,
            self._price_error_delta_items,
            payload.get("error_series", {}),
            control=self._price_error_delta_visibility,
        )
        self._price_error_delta_plot.setLabel("left", "Relative Error" if self._price_error_mode.currentText() == "relative" else "Absolute Error")
        self._price_error_explain.setText(
            f"{meta.get('chart_explanation', 'Model-versus-market price comparison.')}"
            f" Source: {meta.get('data_source', 'routed_greeks')}."
        )
        self._price_error_status.setText(
            f"Model vs Market: expiry={expiry} option={self._price_error_option.currentText()} "
            f"rows={meta.get('rows', 0)} status={meta.get('status', 'ok')}"
        )

    def _refresh_validation_view(self) -> None:
        frame = self._history_frame("surface_points")
        payload = self._cached_page_payload(
            page="validation_workspace",
            key=(
                self._validation_metric.currentText(),
                self._validation_option.currentText(),
                self._validation_expiry.currentText() or "all",
            ),
            builder=lambda: build_surface_validation_payload(
                frame,
                metric=self._validation_metric.currentText(),
                option_type=self._validation_option.currentText(),
                expiry_filter=self._validation_expiry.currentText() or "all",
            ),
        )
        self._update_plot_series(
            self._validation_line_plot,
            self._validation_line_items,
            payload.get("line_series", {}),
            control=self._validation_line_visibility,
        )
        heat = payload.get("heat_image")
        rect = payload.get("rect", (0.0, 0.0, 1.0, 1.0))
        levels = payload.get("levels", (0.0, 1.0))
        meta = payload.get("meta", {})
        show_heat = heat is not None and not bool(meta.get("is_single_expiry"))
        self._validation_heat_plot.setVisible(show_heat)
        if show_heat:
            self._validation_heat_img.setImage(heat, autoLevels=False, autoDownsample=True)
            self._validation_heat_img.setRect(QRectF(*rect))
            self._validation_heat_img.setLevels(levels)
            self._validation_color_bar.setLevels(levels)
            y_ticks = list(zip(meta.get("y_axis_values", []), meta.get("y_axis_labels", [])))
            if y_ticks:
                self._validation_heat_plot.getAxis("left").setTicks([y_ticks])
        self._validation_explain.setText(meta.get("chart_explanation", "Validation workspace."))
        self._validation_status.setText(
            f"Validation Workspace: metric={self._validation_metric.currentText()} "
            f"expiry={meta.get('selected_expiry', self._validation_expiry.currentText() or 'all')} "
            f"rows={meta.get('rows', 0)} status={meta.get('status', 'ok')}"
        )

        summary = self._history_frame("surface_diagnostics")
        if summary.is_empty():
            self._surface_summary_model.update([["No surface summary"]], ["status"])
        else:
            latest = summary.sort([c for c in ("asof_ts", "batch_id") if c in summary.columns]).tail(1)
            cols = [
                c
                for c in (
                    "rows",
                    "price_rmse",
                    "vol_rmse",
                    "atm_mae",
                    "wing_rmse",
                    "within_bid_ask_ratio",
                    "american_within_bid_ask_ratio",
                    "negative_gamma_ratio",
                    "delta_smoothness_violation_ratio",
                    "calendar_violation_ratio",
                    "one_sided_drop_count",
                    "duplicate_conflict_count",
                    "strip_shape_fail_count",
                    "core_eligible_rows",
                )
                if c in latest.columns
            ]
            self._surface_summary_model.update(latest.select(cols).rows(), cols)

    def _refresh_calendar_density_view(self) -> None:
        frame = self._history_frame("surface_points")
        cal_payload = self._cached_page_payload(
            page="calendar_density_heat",
            key=(self._calendar_option.currentText(),),
            builder=lambda: build_calendar_payload(frame, option_type=self._calendar_option.currentText()),
        )
        heat = cal_payload.get("heat_image")
        rect = cal_payload.get("rect", (0.0, 0.0, 1.0, 1.0))
        levels = cal_payload.get("levels", (0.0, 1.0))
        meta = cal_payload.get("meta", {})
        show_heat = heat is not None and not bool(meta.get("is_single_expiry"))
        self._calendar_heat_plot.setVisible(show_heat)
        if show_heat:
            self._calendar_heat_img.setImage(heat, autoLevels=False, autoDownsample=True)
            self._calendar_heat_img.setRect(QRectF(*rect))
            self._calendar_heat_img.setLevels(levels)
            self._calendar_color_bar.setLevels(levels)
            y_ticks = list(zip(meta.get("y_axis_values", []), meta.get("y_axis_labels", [])))
            if y_ticks:
                self._calendar_heat_plot.getAxis("left").setTicks([y_ticks])

        density_payload = self._cached_page_payload(
            page="calendar_density_line",
            key=(self._calendar_option.currentText(), self._density_expiry.currentText() or "all"),
            builder=lambda: build_density_payload(
                frame,
                option_type=self._calendar_option.currentText(),
                expiry_filter=self._density_expiry.currentText() or "all",
            ),
        )
        self._update_plot_series(
            self._density_plot,
            self._density_items,
            density_payload.get("line_series", {}),
            control=self._density_visibility,
        )
        density_meta = density_payload.get("meta", {})
        self._calendar_explain.setText(
            f"{meta.get('chart_explanation', 'Calendar diagnostics.')} "
            f"{density_meta.get('chart_explanation', 'Density diagnostics.')}"
        )
        self._calendar_status.setText(
            f"Calendar / Density: violations={meta.get('violation_count', 0)} "
            f"density_negatives={density_meta.get('negative_points', 0)} "
            f"status={meta.get('status', 'ok')}/{density_meta.get('status', 'ok')}"
        )

        if frame.is_empty():
            self._calendar_violation_model.update([["No calendar diagnostics"]], ["status"])
        else:
            latest = frame.sort([c for c in ("asof_ts", "batch_id") if c in frame.columns]).tail(frame.height)
            violations = latest.filter(pl.col("calendar_violation") == True) if "calendar_violation" in latest.columns else pl.DataFrame()
            if violations.is_empty():
                self._calendar_violation_model.update([["No calendar violations found"]], ["status"])
            else:
                cols = [c for c in ("expiration", "option_type", "strike", "calendar_total_variance", "calendar_violation", "is_negative_gamma", "delta_smoothness_violation") if c in violations.columns]
                self._calendar_violation_model.update(violations.select(cols).head(120).rows(), cols)

    def _refresh_runtime_metrics_view(self) -> None:
        frame = self._history_frame("runtime_metrics")
        payload = self._cached_page_payload(
            page="runtime_metrics",
            key=(),
            builder=lambda: build_runtime_metrics_payload(frame),
        )
        self._update_plot_series(
            self._runtime_metrics_plot,
            self._runtime_metric_items,
            payload.get("line_series", {}),
            control=self._runtime_metric_visibility,
        )
        meta = payload.get("meta", {})
        self._runtime_metrics_explain.setText(meta.get("chart_explanation", "Runtime metrics."))
        self._runtime_metrics_status.setText(
            f"Runtime Metrics: rows={meta.get('rows', 0)} latest_total_ms={meta.get('latest_total_ms', 0.0):.2f} status={meta.get('status', 'ok')}"
        )
        if frame.is_empty():
            self._runtime_metrics_model.update([["No runtime metrics"]], ["status"])
            return
        show_cols = [
            c
            for c in (
                "version",
                "snapshot_kind",
                "total_ms",
                "calibration_ms",
                "pricing_ms",
                "routing_ms",
                "state_bytes_total",
                "drop_overlay",
            )
            if c in frame.columns
        ]
        latest_rows = frame.sort([c for c in ("asof_ts", "version") if c in frame.columns]).tail(20)
        self._runtime_metrics_model.update(latest_rows.select(show_cols).rows(), show_cols)

    def _refresh_scanner_view(self) -> dict[str, Any]:
        focus_summary = self._history_frame("focus_expiry_summary")
        dealer_points = self._history_frame("dealer_exposure_points")
        scanner_levels = self._history_frame("scanner_levels")
        flow_proxy_points = self._history_frame("flow_proxy_points")
        payload = self._cached_page_payload(
            page="short_expiry_scanner",
            key=(self._scanner_selected_focus_label,),
            builder=lambda: build_short_expiry_scanner_payload(
                focus_summary,
                dealer_points,
                scanner_levels,
                flow_proxy_points,
                selected_focus_label=self._scanner_selected_focus_label,
            ),
        )
        meta = payload.get("meta", {})
        selected = str(meta.get("selected_focus_label", self._scanner_selected_focus_label) or self._scanner_selected_focus_label)
        self._scanner_selected_focus_label = selected

        summary_frame = payload.get("summary_frame", pl.DataFrame())
        levels_frame = payload.get("levels_frame", pl.DataFrame())
        flow_frame = payload.get("flow_frame", pl.DataFrame())
        self._sync_scanner_focus_cards(summary_frame, selected)

        heat = payload.get("heat_image")
        rect = payload.get("rect", (0.0, 0.0, 1.0, 1.0))
        levels = payload.get("levels", (0.0, 1.0))
        if heat is not None:
            self._scanner_heat_img.setImage(heat, autoLevels=False, autoDownsample=True)
            self._scanner_heat_img.setRect(QRectF(*rect))
            self._scanner_heat_img.setLevels(levels)
            self._scanner_color_bar.setLevels(levels)
        y_ticks = list(zip(meta.get("y_axis_values", []), meta.get("y_axis_labels", [])))
        if y_ticks:
            self._scanner_heat_plot.getAxis("left").setTicks([y_ticks])

        if summary_frame.is_empty():
            self._scanner_summary_model.update([["No focused expiry summary"]], ["status"])
        else:
            show_cols = [
                c
                for c in (
                    "focus_label",
                    "expiration",
                    "days_to_expiry",
                    "row_count",
                    "eligible_ratio",
                    "within_bid_ask_ratio",
                    "atm_iv_ref",
                    "iv_skew_wing_diff",
                    "volume_sum",
                    "open_interest_sum",
                    "trust_status",
                    "trust_score",
                    "snapshot_age_sec",
                )
                if c in summary_frame.columns
            ]
            self._scanner_summary_model.update(summary_frame.select(show_cols).rows(), show_cols)

        if levels_frame.is_empty():
            self._scanner_levels_model.update([["No scanner levels"]], ["status"])
        else:
            show_cols = [
                c
                for c in (
                    "focus_label",
                    "expiration",
                    "strike",
                    "avg_iv_ref",
                    "avg_market_mid",
                    "total_volume",
                    "total_open_interest",
                    "net_gamma_exposure_oi",
                    "eligible_ratio",
                    "within_bid_ask_ratio",
                    "hotspot_score",
                )
                if c in levels_frame.columns
            ]
            self._scanner_levels_model.update(levels_frame.select(show_cols).rows(), show_cols)

        if flow_frame.is_empty():
            self._scanner_flow_model.update([["No proxy flow diagnostics"]], ["status"])
        else:
            show_cols = [
                c
                for c in (
                    "focus_label",
                    "expiration",
                    "option_type",
                    "strike",
                    "delta_volume",
                    "delta_open_interest",
                    "delta_avg_market_mid",
                    "delta_avg_iv_ref",
                    "delta_gamma_exposure_oi",
                    "proxy_confidence",
                    "proxy_reason",
                )
                if c in flow_frame.columns
            ]
            self._scanner_flow_model.update(flow_frame.select(show_cols).rows(), show_cols)

        runtime_payload = self._live_runtime_status_callback() if self._live_runtime_status_callback is not None else {}
        runtime_payload = runtime_payload or {}
        cadence_mode = str(runtime_payload.get("cadence_mode", "n/a"))
        fetch_scope = str(runtime_payload.get("fetch_scope", "n/a"))
        hot_seconds = int(runtime_payload.get("cadence_hot_seconds", 0) or 0)
        full_seconds = int(runtime_payload.get("cadence_full_snapshot_seconds", 0) or 0)
        runtime_state = str(runtime_payload.get("state", "idle"))
        self._scanner_runtime_badge.setText(
            f"Scanner runtime: state={runtime_state} scope={fetch_scope} cadence={cadence_mode} hot={hot_seconds}s full={full_seconds}s"
        )
        trust_status = str(meta.get("trust_status", "n/a"))
        trust_score = float(meta.get("trust_score", float("nan")) or float("nan"))
        snapshot_age = float(meta.get("snapshot_age_sec", float("nan")) or float("nan"))
        age_text = f"{snapshot_age:.1f}s" if np.isfinite(snapshot_age) else "n/a"
        trust_text = f"{trust_score:.1f}" if np.isfinite(trust_score) else "n/a"
        self._scanner_explain.setText(meta.get("chart_explanation", self._scanner_explain.text()))
        self._scanner_status.setText(
            f"SPY Short Expiry Scanner: focus={selected} expiry={meta.get('selected_expiration', 'n/a')} "
            f"trust={trust_status} score={trust_text} snapshot_age={age_text} status={meta.get('status', 'ok')}"
        )
        return payload

    def _on_scanner_focus_card_clicked(self, focus_label: str) -> None:
        self._scanner_selected_focus_label = focus_label
        payload = self._refresh_scanner_view()
        meta = payload.get("meta", {})
        selected_expiration = str(meta.get("selected_expiration", "") or "")
        if selected_expiration and selected_expiration.lower() != "n/a":
            self._apply_scanner_drilldown(selected_expiration)

    def _sync_scanner_focus_cards(self, summary_frame: pl.DataFrame, selected_focus_label: str) -> None:
        lookup: dict[str, dict[str, object]] = {}
        if not summary_frame.is_empty() and "focus_label" in summary_frame.columns:
            for row in summary_frame.to_dicts():
                lookup[str(row.get("focus_label", ""))] = row
        for focus_label, button in self._scanner_focus_buttons.items():
            row = lookup.get(focus_label)
            is_selected = focus_label == selected_focus_label
            button.blockSignals(True)
            button.setChecked(is_selected)
            button.blockSignals(False)
            if row is None:
                button.setEnabled(False)
                button.setText(f"{focus_label}\nUnavailable")
                button.setStyleSheet(self._scanner_card_style("unavailable", is_selected))
                continue
            button.setEnabled(True)
            trust_status = str(row.get("trust_status", "review"))
            trust_score = float(row.get("trust_score", float("nan")) or float("nan"))
            snapshot_age = float(row.get("snapshot_age_sec", float("nan")) or float("nan"))
            score_text = f"{trust_score:.1f}" if np.isfinite(trust_score) else "n/a"
            age_text = f"{snapshot_age:.0f}s" if np.isfinite(snapshot_age) else "n/a"
            button.setText(
                f"{focus_label}\n{row.get('expiration', 'n/a')}  trust={trust_status}\n"
                f"score={score_text}  age={age_text}"
            )
            button.setStyleSheet(self._scanner_card_style(trust_status, is_selected))

    def _scanner_card_style(self, trust_status: str, is_selected: bool) -> str:
        palette = {
            "trusted": ("#edf9ef", "#2e7d32"),
            "review": ("#fff8e1", "#f9a825"),
            "caution": ("#fdecea", "#c62828"),
            "unavailable": ("#f4f5f7", "#78909c"),
        }
        background, accent = palette.get(trust_status, palette["review"])
        border_width = "2px" if is_selected else "1px"
        return (
            "QPushButton {"
            f"background-color: {background};"
            f"border: {border_width} solid {accent};"
            "border-radius: 8px;"
            "padding: 10px;"
            "text-align: left;"
            "font-weight: 600;"
            "}"
        )

    def _apply_scanner_drilldown(self, expiration: str) -> None:
        self._set_combo_text(self._overlay_expiry, expiration, fallback="all")
        self._set_combo_text(self._price_error_expiry, expiration)
        self._set_combo_text(self._validation_expiry, expiration, fallback="all")
        self._set_combo_text(self._density_expiry, expiration)
        self._set_combo_text(self._temporal_expiry, expiration)
        self._request_overlay_refresh()
        self._refresh_price_error_plot()
        self._refresh_validation_view()
        self._refresh_calendar_density_view()
        self._refresh_temporal_plot()

    def _set_combo_text(self, combo: QComboBox, target: str, *, fallback: str | None = None) -> None:
        idx = combo.findText(target)
        if idx < 0 and fallback is not None:
            idx = combo.findText(fallback)
        if idx < 0:
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _selected_engine_mask(self) -> set[str]:
        mapping = {
            "fdm": {"fdm_cn_log_cpp", "crank_nicolson_log", "crank_nicolson_linear"},
            "tree": {"binomial_richardson"},
            "bs2002": {"bs2002_cpp"},
            "rim": {"rim_fd"},
            "laplace": {"laplace_zhu", "laplace_zhu_cpp"},
        }
        selected: set[str] = set()
        for key, cb in self._engine_toggles.items():
            if cb.isChecked():
                selected.update(mapping.get(key, set()))
        return selected

    def _refresh_live_runtime_status(self) -> None:
        if self._live_runtime_status_callback is None:
            return
        payload = self._live_runtime_status_callback() or {}
        state = str(payload.get("state", "idle"))
        message = str(payload.get("message", "waiting for live status"))
        expiration = str(payload.get("expiration", "auto") or "auto")
        fetch_scope = str(payload.get("fetch_scope", "n/a") or "n/a")
        cadence_mode = str(payload.get("cadence_mode", "n/a") or "n/a")
        hot_seconds = int(payload.get("cadence_hot_seconds", 0) or 0)
        full_seconds = int(payload.get("cadence_full_snapshot_seconds", 0) or 0)
        cadence_suffix = f" scope={fetch_scope} cadence={cadence_mode} hot={hot_seconds}s full={full_seconds}s"
        if state == "ok":
            rows = int(payload.get("rows", 0) or 0)
            latency_ms = float(payload.get("latency_ms", 0.0) or 0.0)
            self._live_runtime_status.setText(
                f"Live runtime: ok expiry={expiration} rows={rows} latency_ms={latency_ms:.2f}{cadence_suffix}"
            )
            return
        if state == "empty":
            self._live_runtime_status.setText(
                f"Live runtime: no rows returned for expiry={expiration}. Try choosing an explicit expiry from the dropdown.{cadence_suffix}"
            )
            return
        if state == "error":
            error_type = str(payload.get("error_type", "error"))
            self._live_runtime_status.setText(
                f"Live runtime: {error_type} while polling expiry={expiration}: {message}{cadence_suffix}"
            )
            return
        self._live_runtime_status.setText(f"Live runtime: {message}{cadence_suffix}")

    def _configure_live_expiration_controls(self, symbol: str, selected: str | None) -> None:
        if self._expiration_lookup_callback is None:
            configure_expiration_combo(
                self._live_expiration,
                [],
                auto_label="Auto (configured scope)",
                disabled_label="Live expiry lookup unavailable",
            )
            self._live_expiration_status.setText("Live expiry: lookup is unavailable in this runtime.")
            return
        if not self._live_expiration_enabled:
            configure_expiration_combo(
                self._live_expiration,
                [],
                auto_label="Auto (configured scope)",
                disabled_label="Live expiry available in ui_live during market hours",
            )
            self._live_expiration_status.setText(
                "Live expiry: start ui_live during market hours to switch the live chain interactively."
            )
            return
        expirations = [str(exp).strip() for exp in self._expiration_lookup_callback(symbol) if str(exp).strip()]
        configure_expiration_combo(
            self._live_expiration,
            expirations,
            selected=selected,
            auto_label="Auto (configured scope)",
            enabled=True,
        )
        if expirations:
            choice = selected or "auto"
            self._live_expiration_status.setText(
                f"Live expiry: current selection={choice}. Changes apply on the next live poll."
            )
        else:
            self._live_expiration_status.setText(
                "Live expiry: yfinance did not return explicit expiries yet, so Auto will keep using the configured scope."
            )

    def _on_live_expiration_changed(self) -> None:
        if self._live_expiration_setter is None:
            return
        expiration = self._live_expiration.currentData()
        selected = str(expiration).strip() if expiration else None
        self._live_expiration_setter(selected)
        label = selected or "auto"
        self._live_expiration_status.setText(
            f"Live expiry: current selection={label}. Changes apply on the next live poll."
        )

    def _refresh_latest_snapshot(self) -> None:
        if self._refresh_callback is None:
            self._snapshot_status.setText("Snapshot status: no refresh callback configured")
            return
        try:
            message = self._refresh_callback()
            self._snapshot_status.setText(f"Snapshot status: {message}")
        except Exception as exc:  # pragma: no cover
            logger.exception("snapshot_refresh_failed err=%s", exc)
            self._snapshot_status.setText(f"Snapshot status: refresh failed: {exc}")

    def _runtime_summary_text(self) -> str:
        if not self._runtime_summary:
            return "Runtime summary: unavailable"
        ordered = [
            "app_mode",
            "ticker",
            "expiration",
            "refresh_ms",
            "runtime_mode",
            "ssvi_backend",
            "fdm_backend",
            "live_poll_seconds",
            "live_focus_labels",
            "live_hot_poll_seconds",
            "live_full_snapshot_poll_seconds",
            "stream_lock_enforced",
            "provider_config",
            "pipeline_config",
            "session_state_path",
        ]
        lines = []
        for key in ordered:
            if key in self._runtime_summary:
                lines.append(f"{key}={self._runtime_summary[key]}")
        for key, value in self._runtime_summary.items():
            if key not in ordered:
                lines.append(f"{key}={value}")
        return "Runtime summary:\n" + "\n".join(lines)

    def _collect_session_config(self) -> dict[str, Any]:
        return {
            "mode": self._run_mode.currentText(),
            "ticker": self._run_symbol_controls.ticker(),
            "expiration": self._run_symbol_controls.expiration() or "",
            "refresh_ms": int(self._run_refresh_ms.value()),
            "allow_shared": self._run_allow_shared.isChecked(),
            "provider_config": self._run_provider_config.text().strip(),
            "pipeline_config": self._run_pipeline_config.text().strip(),
        }

    def _apply_display_settings(self) -> None:
        refresh_ms = max(int(self._run_refresh_ms.value()), 10)
        self._timer.setInterval(refresh_ms)
        self._session_config.update(self._collect_session_config())
        self._runtime_summary["refresh_ms"] = refresh_ms
        self._run_runtime_summary.setText(self._runtime_summary_text())
        self._run_config_status.setText(
            f"Run Config: applied UI refresh interval={refresh_ms}ms now. Other changes will take effect next launch."
        )

    def _save_session_settings(self) -> None:
        payload = self._collect_session_config()
        self._session_config.update(payload)
        if self._session_save_callback is None:
            self._run_config_status.setText("Run Config: no session-save callback is configured.")
            return
        try:
            message = self._session_save_callback(payload)
        except Exception as exc:  # pragma: no cover
            logger.exception("session_save_failed err=%s", exc)
            self._run_config_status.setText(f"Run Config: failed to save session settings: {exc}")
            return
        self._run_config_status.setText(f"Run Config: {message}")

    def _schedule_notice(self, snapshot: SymbolSnapshot) -> str:
        now_et = datetime.now(ZoneInfo(self._snapshot_timezone))
        hhmm = now_et.strftime("%H:%M")
        if hhmm >= self._oi_refresh_time and snapshot.snapshot_kind != "eod_oi_refresh":
            return "OI refresh checkpoint is due; use the refresh button to reconcile OI without recomputing unless prices changed."
        if hhmm >= self._final_prices_refresh_time and snapshot.snapshot_kind not in {"eod_final_refresh", "eod_oi_refresh"}:
            return "Final-price refresh checkpoint is due; use the refresh button to fetch final prices and recompute Greeks only if price inputs changed."
        if hhmm >= self._market_close_freeze_time and snapshot.snapshot_kind not in {"eod_final", "eod_final_refresh", "eod_oi_refresh", "offline_bootstrap"}:
            return "Live polling is frozen for the day; showing the latest stored batch until the final-price refresh window."
        return ""

    def _refresh_button_text(self) -> str:
        now_et = datetime.now(ZoneInfo(self._snapshot_timezone))
        hhmm = now_et.strftime("%H:%M")
        if hhmm >= self._oi_refresh_time:
            return "Refresh Final / OI State"
        if hhmm >= self._final_prices_refresh_time:
            return "Refresh Final Prices"
        if hhmm >= self._market_close_freeze_time:
            return "Reload Stored Final Snapshot"
        return "Refresh Latest Snapshot"

    def _on_greeks_row_selected(self, index: QModelIndex) -> None:
        snapshot = self._cache.get_snapshot_nowait(self._state.symbol)
        if snapshot is None or snapshot.greeks.is_empty():
            return
        show_cols = [c for c in self._greeks_model._columns if c in snapshot.greeks.columns]
        if not show_cols:
            return
        visible = snapshot.greeks.select(show_cols).head(120)
        if index.row() >= visible.height:
            return
        row = visible.row(index.row(), named=True)
        detail = (
            f"engine={row.get('engine_used', '')} model_price={row.get('model_price', row.get('price', ''))} "
            f"market_mid={row.get('market_mid', '')} rate={row.get('rate_used', '')} "
            f"dividend={row.get('dividend_used', '')} tau={row.get('tau_years', '')} "
            f"theta={row.get('theta', '')} ({ROUTED_GREEKS_COLUMN_HELP.get('theta', '')}) "
            f"vega_method={row.get('vega_method', '')} rho_method={row.get('rho_method', '')} "
            f"batch_id={row.get('batch_id', '')}"
        )
        self._greeks_detail.setText(detail)

    def _refresh_temporal_controls(self) -> None:
        history = self._history_frame("greeks")
        if history.is_empty() and (snapshot := self._cache.get_snapshot_nowait(self._state.symbol)) is not None:
            history = snapshot.greeks
        if history.is_empty() or "expiration" not in history.columns:
            self._temporal_status.setText("Temporal Greeks: no routed Greeks history")
            return
        expiries = sorted({str(x) for x in history["expiration"].to_list() if x is not None})
        current = self._temporal_expiry.currentText() if self._temporal_expiry.count() > 0 else ""
        self._temporal_expiry.blockSignals(True)
        self._temporal_expiry.clear()
        for exp in expiries:
            self._temporal_expiry.addItem(exp)
        idx = self._temporal_expiry.findText(current)
        self._temporal_expiry.setCurrentIndex(idx if idx >= 0 else 0)
        self._temporal_expiry.blockSignals(False)
        self._refresh_temporal_plot()

    def _refresh_temporal_plot(self) -> None:
        history = self._history_frame("greeks")
        if history.is_empty() and (snapshot := self._cache.get_snapshot_nowait(self._state.symbol)) is not None:
            history = snapshot.greeks
        if history.is_empty():
            self._temporal_status.setText("Temporal Greeks: no routed Greeks history")
            return
        expiry = self._temporal_expiry.currentText()
        greek = self._temporal_greek.currentText()
        if not expiry or greek not in history.columns:
            return
        frame = history.filter(pl.col("expiration").cast(pl.String) == expiry)
        option_choice = self._overlay_opt_type.currentText()
        if option_choice != "all" and "option_type" in frame.columns:
            frame = frame.filter(pl.col("option_type") == option_choice)
        engine_mask = self._selected_engine_mask()
        if engine_mask and "engine_used" in frame.columns:
            frame = frame.filter(pl.col("engine_used").is_in(sorted(engine_mask)))
        if frame.is_empty() or "asof_ts" not in frame.columns:
            self._temporal_status.setText("Temporal Greeks: no rows for selected expiry/filters")
            return
        timestamps = sorted({x for x in frame["asof_ts"].to_list() if x is not None})
        if not timestamps:
            self._temporal_status.setText("Temporal Greeks: no timestamps available")
            return
        self._temporal_slider.blockSignals(True)
        self._temporal_slider.setMaximum(max(len(timestamps) - 1, 0))
        self._temporal_slider.setValue(min(self._temporal_slider.value(), max(len(timestamps) - 1, 0)))
        self._temporal_slider.blockSignals(False)
        idx = min(self._temporal_slider.value(), len(timestamps) - 1)
        ts = timestamps[idx]
        slice_frame = frame.filter(pl.col("asof_ts") == ts).sort("strike")
        if slice_frame.is_empty():
            self._temporal_status.setText("Temporal Greeks: selected timestamp slice is empty")
            return
        x = np.asarray(slice_frame["strike"].to_list(), dtype=np.float32)
        y = np.asarray(slice_frame[greek].to_list(), dtype=np.float32)
        self._temporal_line.setData(x, y)
        self._temporal_plot.setLabel("left", greek)
        self._temporal_time_label.setText(f"Time: {ts}")
        self._temporal_explain.setText(
            "Temporal source: routed_greeks history from cache plus persisted parquet history. "
            f"X=strike, Y={greek}, slider=batch timestamp, selected expiry={expiry}."
        )
        self._temporal_status.setText(
            f"Temporal Greeks: expiry={expiry} greek={greek} points={slice_frame.height} samples={len(timestamps)}"
        )


def run_ui(
    cache: InMemoryQuoteCache,
    refresh_ms: int = 50,
    symbol: str = "SPY",
    bridge: UIStateBridge | None = None,
    default_space_mode: str = "residual",
    dual_mode_enabled: bool = False,
    ui_apply_p95_limit_ms: float = 50.0,
    ui_auto_degrade: bool = True,
    refresh_callback: Callable[[], str] | None = None,
    history_callback: Callable[[str, str], pl.DataFrame] | None = None,
    snapshot_timezone: str = "America/New_York",
    market_close_freeze_time: str = "17:00",
    final_prices_refresh_time: str = "17:30",
    oi_refresh_time: str = "20:30",
    session_config: dict[str, Any] | None = None,
    runtime_summary: dict[str, Any] | None = None,
    session_save_callback: Callable[[dict[str, Any]], str] | None = None,
    bootstrap_message: str | None = None,
    symbol_search_callback: Callable[[str], list[dict[str, str]]] | None = None,
    expiration_lookup_callback: Callable[[str], list[str]] | None = None,
    live_expiration: str | None = None,
    live_expiration_setter: Callable[[str | None], None] | None = None,
    live_expiration_enabled: bool = False,
    live_runtime_status_callback: Callable[[], dict[str, object]] | None = None,
    app: QApplication | None = None,
) -> int:
    qt_app = app or QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(
        cache=cache,
        refresh_ms=refresh_ms,
        symbol=symbol,
        bridge=bridge,
        default_space_mode=default_space_mode,
        dual_mode_enabled=dual_mode_enabled,
        ui_apply_p95_limit_ms=ui_apply_p95_limit_ms,
        ui_auto_degrade=ui_auto_degrade,
        refresh_callback=refresh_callback,
        history_callback=history_callback,
        snapshot_timezone=snapshot_timezone,
        market_close_freeze_time=market_close_freeze_time,
        final_prices_refresh_time=final_prices_refresh_time,
        oi_refresh_time=oi_refresh_time,
        session_config=session_config,
        runtime_summary=runtime_summary,
        session_save_callback=session_save_callback,
        bootstrap_message=bootstrap_message,
        symbol_search_callback=symbol_search_callback,
        expiration_lookup_callback=expiration_lookup_callback,
        live_expiration=live_expiration,
        live_expiration_setter=live_expiration_setter,
        live_expiration_enabled=live_expiration_enabled,
        live_runtime_status_callback=live_runtime_status_callback,
    )
    window.resize(1280, 860)
    window.show()
    return qt_app.exec()

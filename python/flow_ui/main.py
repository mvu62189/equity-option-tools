from __future__ import annotations

import logging
import sys
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
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
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from flow_core.orchestration.cache import InMemoryQuoteCache
from flow_core.orchestration.state_store import SymbolSnapshot
from flow_core.quant import scan_arbitrage_violations
from flow_ui.state_bridge import UIStateBridge
from flow_ui.update_coordinator import UpdateCoordinator
from flow_ui.viewmodels import build_price_error_payload

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class UIState:
    symbol: str = "SPY"
    health: str = "idle"
    last_version: int = 0


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
    ) -> None:
        super().__init__()
        self._cache = cache
        self._state = UIState(symbol=symbol)
        self._bridge = bridge or UIStateBridge(max_pending_per_symbol=1)
        self._coordinator = UpdateCoordinator()
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

        self._cache.set_update_callback(self._bridge.coalesce)
        self._bridge.snapshot_ready.connect(self._on_snapshot_ready)
        self._coordinator.overlay_ready.connect(self._on_overlay_ready)

        self.setWindowTitle("Quant Pipeline MVP")
        tabs = QTabWidget()
        self.setCentralWidget(tabs)

        self._live_label = QLabel("Live monitor: waiting for data")
        self._snapshot_status = QLabel("Snapshot status: waiting for data")
        self._refresh_button = QPushButton("Refresh Latest Snapshot")
        self._refresh_button.setText(self._refresh_button_text())
        self._refresh_button.clicked.connect(self._refresh_latest_snapshot)
        live_page = QWidget()
        live_layout = QVBoxLayout(live_page)
        live_layout.addWidget(self._live_label)
        live_layout.addWidget(self._snapshot_status)
        live_layout.addWidget(self._refresh_button)

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
        self._price_error_delta_plot = pg.PlotWidget()
        self._price_error_delta_plot.setBackground("w")
        self._price_error_delta_plot.setLabel("bottom", "Strike")
        self._price_error_delta_plot.setLabel("left", "Error")
        self._price_error_delta_plot.addLegend(offset=(10, 10))
        self._price_error_delta_items: dict[str, pg.PlotDataItem] = {}
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
        price_layout.addWidget(self._price_error_delta_plot)

        tabs.addTab(live_page, "Live Chain")
        tabs.addTab(iv_page, "SSVI vs Baseline")
        tabs.addTab(greeks_page, "Routed Greeks")
        tabs.addTab(overlay_page, "Greeks Overlay")
        tabs.addTab(price_page, "Model vs Market")
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
        if frame.is_empty():
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
            self._refresh_price_error_controls(greeks)
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
            self._refresh_price_error_plot()

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

    def _refresh_price_error_controls(self, greeks) -> None:  # noqa: ANN001
        if "expiration" not in greeks.columns:
            return
        expiries = sorted({str(x) for x in greeks["expiration"].to_list() if x is not None})
        current = self._price_error_expiry.currentText() if self._price_error_expiry.count() > 0 else ""
        self._price_error_expiry.blockSignals(True)
        self._price_error_expiry.clear()
        for exp in expiries:
            self._price_error_expiry.addItem(exp)
        idx = self._price_error_expiry.findText(current)
        self._price_error_expiry.setCurrentIndex(idx if idx >= 0 else 0)
        self._price_error_expiry.blockSignals(False)

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
        self._update_plot_series(self._overlay_line_plot, self._line_items, line_series)

    def _update_plot_series(
        self,
        plot: pg.PlotWidget,
        storage: dict[str, pg.PlotDataItem],
        line_series: dict[str, object],
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

    def _refresh_price_error_plot(self) -> None:
        snapshot = self._cache.get_snapshot_nowait(self._state.symbol)
        if snapshot is None or snapshot.greeks.is_empty():
            self._price_error_status.setText("Model vs Market: no routed Greeks available")
            return
        expiry = self._price_error_expiry.currentText() or "all"
        payload = build_price_error_payload(
            snapshot,
            option_type=self._price_error_option.currentText(),
            expiry_filter=expiry,
            engine_mask=self._selected_engine_mask(),
            relative=self._price_error_mode.currentText() == "relative",
        )
        meta = payload.get("meta", {})
        self._update_plot_series(self._price_error_plot, self._price_error_line_items, payload.get("line_series", {}))
        self._update_plot_series(self._price_error_delta_plot, self._price_error_delta_items, payload.get("error_series", {}))
        self._price_error_delta_plot.setLabel("left", "Relative Error" if self._price_error_mode.currentText() == "relative" else "Absolute Error")
        self._price_error_explain.setText(
            f"{meta.get('chart_explanation', 'Model-versus-market price comparison.')}"
            f" Source: {meta.get('data_source', 'routed_greeks')}."
        )
        self._price_error_status.setText(
            f"Model vs Market: expiry={expiry} option={self._price_error_option.currentText()} "
            f"rows={meta.get('rows', 0)} status={meta.get('status', 'ok')}"
        )

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
) -> int:
    app = QApplication(sys.argv)
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
    )
    window.resize(1280, 860)
    window.show()
    return app.exec()

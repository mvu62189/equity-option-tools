from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from flow_ui.symbol_controls import SymbolExpirationControls

from .runtime import AppMode, LaunchConfig

TickerSearchCallback = Callable[[str], list[dict[str, str]]]
ExpirationLookupCallback = Callable[[str], list[str]]


class LaunchDialog(QDialog):
    def __init__(
        self,
        initial: LaunchConfig,
        *,
        symbol_search_callback: TickerSearchCallback | None = None,
        expiration_lookup_callback: ExpirationLookupCallback | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Start Quant Pipeline MVP")
        self._initial = initial.normalized()

        self._mode = QComboBox()
        self._mode.addItems([mode.value for mode in AppMode])
        self._mode.setCurrentText(self._initial.mode.value)

        self._symbol_controls = SymbolExpirationControls(
            ticker=self._initial.ticker,
            expiration=self._initial.expiration,
            search_callback=symbol_search_callback,
            expiration_lookup_callback=expiration_lookup_callback,
        )
        self._refresh_ms = QSpinBox()
        self._refresh_ms.setRange(0, 60_000)
        self._refresh_ms.setSingleStep(25)
        self._refresh_ms.setValue(self._initial.refresh_ms)
        self._allow_shared = QCheckBox("Allow shared stream lock")
        self._allow_shared.setChecked(self._initial.allow_shared)
        self._provider_config = QLineEdit(self._initial.provider_config)
        self._pipeline_config = QLineEdit(self._initial.pipeline_config)
        self._mode.currentTextChanged.connect(self._on_mode_changed)

        form = QFormLayout()
        form.addRow("App Mode", self._mode)
        form.addRow("Ticker", self._symbol_controls.ticker_input)
        form.addRow("Expiration", self._symbol_controls.expiration_combo)
        form.addRow("Refresh (ms)", self._refresh_ms)
        form.addRow("", self._allow_shared)
        form.addRow("Provider Config", self._provider_config)
        form.addRow("Pipeline Config", self._pipeline_config)

        note = QLabel(
            "Choose how to start the app. Type a ticker to see the top yfinance matches, then pick from the live expiry list. "
            "`snapshot_once` always pulls the full surface across every expiry."
        )
        note.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.resize(560, 300)
        self._on_mode_changed(self._mode.currentText())

    def _on_mode_changed(self, mode: str) -> None:
        self._symbol_controls.set_mode(mode)

    def launch_config(self) -> LaunchConfig:
        return LaunchConfig(
            mode=AppMode(self._mode.currentText()),
            ticker=self._symbol_controls.ticker(),
            expiration=self._symbol_controls.expiration(),
            refresh_ms=self._refresh_ms.value(),
            allow_shared=self._allow_shared.isChecked(),
            provider_config=self._provider_config.text(),
            pipeline_config=self._pipeline_config.text(),
        ).normalized()


def prompt_for_launch_config(
    initial: LaunchConfig,
    *,
    app: Any | None = None,
    symbol_search_callback: TickerSearchCallback | None = None,
    expiration_lookup_callback: ExpirationLookupCallback | None = None,
) -> LaunchConfig | None:
    _ = app
    dialog = LaunchDialog(
        initial,
        symbol_search_callback=symbol_search_callback,
        expiration_lookup_callback=expiration_lookup_callback,
    )
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.launch_config()

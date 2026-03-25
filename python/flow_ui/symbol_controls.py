from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer, Qt, QStringListModel
from PySide6.QtWidgets import QComboBox, QCompleter, QLineEdit

TickerSearchCallback = Callable[[str], list[dict[str, str]]]
ExpirationLookupCallback = Callable[[str], list[str]]

AUTO_EXPIRY_LABEL = "Auto (configured scope)"
SNAPSHOT_EXPIRY_LABEL = "Full surface snapshot (all expiries)"
CHOOSE_TICKER_LABEL = "Choose ticker first"
NO_EXPIRIES_LABEL = "No yfinance expiries found"


def configure_expiration_combo(
    combo: QComboBox,
    expirations: list[str],
    *,
    selected: str | None = None,
    auto_label: str | None = AUTO_EXPIRY_LABEL,
    disabled_label: str | None = None,
    enabled: bool = True,
) -> None:
    current = selected or ""
    combo.blockSignals(True)
    combo.clear()
    if disabled_label is not None:
        combo.addItem(disabled_label, None)
        combo.setCurrentIndex(0)
        combo.setEnabled(False)
        combo.blockSignals(False)
        return
    if auto_label is not None:
        combo.addItem(auto_label, None)
    for exp in expirations:
        combo.addItem(exp, exp)
    if current:
        idx = combo.findData(current)
        if idx < 0:
            idx = combo.findText(current)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
    else:
        combo.setCurrentIndex(0)
    combo.setEnabled(enabled)
    combo.blockSignals(False)


class SymbolExpirationControls:
    def __init__(
        self,
        *,
        ticker: str = "SPY",
        expiration: str | None = None,
        search_callback: TickerSearchCallback | None = None,
        expiration_lookup_callback: ExpirationLookupCallback | None = None,
    ) -> None:
        self._search_callback = search_callback
        self._expiration_lookup_callback = expiration_lookup_callback
        self._mode = "ui_review"
        self._suggestions: dict[str, str] = {}

        self.ticker_input = QLineEdit(ticker)
        self.expiration_combo = QComboBox()
        self.expiration_combo.setEditable(False)

        self._model = QStringListModel(self.ticker_input)
        self._completer = QCompleter(self._model, self.ticker_input)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self.ticker_input.setCompleter(self._completer)

        self._search_timer = QTimer(self.ticker_input)
        self._search_timer.setInterval(250)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self._refresh_suggestions)

        self.ticker_input.textEdited.connect(self._queue_search)
        self.ticker_input.editingFinished.connect(self._commit_ticker)
        self._completer.activated.connect(self._apply_suggestion)

        configure_expiration_combo(self.expiration_combo, [], disabled_label=CHOOSE_TICKER_LABEL)
        self.set_mode("ui_review")
        if self.ticker():
            self._load_expirations(preferred=expiration)

    def ticker(self) -> str:
        return self.ticker_input.text().strip().upper()

    def expiration(self) -> str | None:
        data = self.expiration_combo.currentData()
        return str(data).strip() if data else None

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        if mode == "snapshot_once":
            configure_expiration_combo(self.expiration_combo, [], auto_label=None, disabled_label=SNAPSHOT_EXPIRY_LABEL)
            return
        if not self.ticker():
            configure_expiration_combo(self.expiration_combo, [], disabled_label=CHOOSE_TICKER_LABEL)
            return
        self._load_expirations(preferred=self.expiration())

    def _queue_search(self) -> None:
        if self._search_callback is None:
            return
        self._search_timer.start()

    def _refresh_suggestions(self) -> None:
        if self._search_callback is None:
            return
        query = self.ticker_input.text().strip()
        if not query:
            self._suggestions.clear()
            self._model.setStringList([])
            return
        results = self._search_callback(query) or []
        labels: list[str] = []
        self._suggestions.clear()
        for result in results[:5]:
            symbol = str(result.get("symbol") or "").strip().upper()
            label = str(result.get("label") or symbol).strip()
            if not symbol or not label:
                continue
            labels.append(label)
            self._suggestions[label] = symbol
        self._model.setStringList(labels)
        if labels:
            self._completer.complete()

    def _apply_suggestion(self, label: str) -> None:
        symbol = self._suggestions.get(str(label), str(label).split(" ", 1)[0]).strip().upper()
        self.ticker_input.setText(symbol)
        self._load_expirations(preferred=None)

    def _commit_ticker(self) -> None:
        symbol = self.ticker()
        self.ticker_input.setText(symbol)
        if self._mode == "snapshot_once":
            configure_expiration_combo(self.expiration_combo, [], auto_label=None, disabled_label=SNAPSHOT_EXPIRY_LABEL)
            return
        if not symbol:
            configure_expiration_combo(self.expiration_combo, [], disabled_label=CHOOSE_TICKER_LABEL)
            return
        self._load_expirations(preferred=self.expiration())

    def _load_expirations(self, *, preferred: str | None) -> None:
        symbol = self.ticker()
        if self._mode == "snapshot_once":
            configure_expiration_combo(self.expiration_combo, [], auto_label=None, disabled_label=SNAPSHOT_EXPIRY_LABEL)
            return
        if not symbol:
            configure_expiration_combo(self.expiration_combo, [], disabled_label=CHOOSE_TICKER_LABEL)
            return
        expirations = self._expiration_lookup_callback(symbol) if self._expiration_lookup_callback is not None else []
        normalized = [str(exp).strip() for exp in expirations if str(exp).strip()]
        if not normalized:
            configure_expiration_combo(
                self.expiration_combo,
                [],
                selected=None,
                auto_label=AUTO_EXPIRY_LABEL,
                enabled=True,
            )
            return
        configure_expiration_combo(self.expiration_combo, normalized, selected=preferred, auto_label=AUTO_EXPIRY_LABEL)

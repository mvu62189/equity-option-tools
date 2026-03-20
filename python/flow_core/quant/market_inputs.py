from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import numpy as np
import yfinance as yf
from scipy.interpolate import PchipInterpolator

from .dividends import DividendEvent


def _latest_price(symbol: str) -> float:
    ticker = yf.Ticker(symbol)
    try:
        fast = getattr(ticker, "fast_info", {})
        if isinstance(fast, dict):
            val = fast.get("lastPrice")
            if val is not None and np.isfinite(val):
                return float(val)
    except Exception:
        pass

    hist = ticker.history(period="5d", interval="1d", auto_adjust=False)
    if hist is None or hist.empty:
        raise RuntimeError(f"no price history for {symbol}")
    close = hist["Close"].dropna()
    if close.empty:
        raise RuntimeError(f"no close values for {symbol}")
    return float(close.iloc[-1])


@dataclass(slots=True)
class TBillRateCurve:
    refresh_seconds: int = 900
    use_pchip: bool = True
    _asof_ts: datetime | None = field(default=None, init=False, repr=False)
    _irx: float | None = field(default=None, init=False, repr=False)
    _x: np.ndarray = field(default_factory=lambda: np.array([], dtype=float), init=False, repr=False)
    _y: np.ndarray = field(default_factory=lambda: np.array([], dtype=float), init=False, repr=False)
    _interp: PchipInterpolator | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        now = datetime.now(timezone.utc)
        if not force and self._asof_ts is not None:
            age = (now - self._asof_ts).total_seconds()
            if age < max(10, self.refresh_seconds):
                return

        nodes = {
            0.25: _latest_price("^IRX") / 100.0,
            5.0: _latest_price("^FVX") / 100.0,
            10.0: _latest_price("^TNX") / 100.0,
        }

        xs = np.array(sorted(nodes.keys()), dtype=float)
        ys = np.array([float(nodes[x]) for x in xs], dtype=float)

        self._irx = float(nodes[0.25])
        self._x = xs
        self._y = ys
        self._asof_ts = now
        self._interp = PchipInterpolator(xs, ys, extrapolate=True) if self.use_pchip else None

    def rate(self, tau_years: float) -> float:
        if self._asof_ts is None or self._x.size == 0:
            self.refresh(force=True)
        else:
            self.refresh(force=False)

        tau = max(float(tau_years), 1e-8)
        if tau < 0.25 and self._irx is not None:
            return float(self._irx)

        assert self._x.size > 0 and self._y.size > 0
        if self._interp is not None:
            val = float(self._interp(tau))
        else:
            val = float(np.interp(tau, self._x, self._y))
        return float(np.clip(val, -0.05, 0.25))


@dataclass(slots=True)
class HybridDividendSource:
    projection_horizon_years: float = 3.0
    lookback_events: int = 8
    _cache: dict[str, tuple[datetime, list[tuple[date, float]]]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        pass

    @staticmethod
    def _normalize_date(dt: object) -> date | None:
        if isinstance(dt, datetime):
            return dt.date()
        if isinstance(dt, date):
            return dt
        return None

    def _infer_frequency_days(self, dates: list[date]) -> int:
        if len(dates) < 3:
            return 91
        deltas = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        pos = [d for d in deltas if d > 0]
        if not pos:
            return 91
        med = int(round(float(np.median(np.array(pos, dtype=float)))))
        return min(max(med, 20), 370)

    def _project_schedule(self, symbol: str, asof: datetime, horizon_years: float) -> list[tuple[date, float]]:
        ticker = yf.Ticker(symbol)
        raw = ticker.dividends
        if raw is None or len(raw) == 0:  # type: ignore[arg-type]
            return []

        hist: list[tuple[date, float]] = []
        for idx, val in raw.items():  # type: ignore[assignment]
            d = self._normalize_date(idx)
            if d is None:
                continue
            amt = float(val)
            if amt > 0.0 and np.isfinite(amt):
                hist.append((d, amt))

        if not hist:
            return []
        hist = sorted(hist, key=lambda x: x[0])
        recent = hist[-max(3, self.lookback_events) :]
        dates = [x[0] for x in recent]
        amts = [x[1] for x in recent]

        freq_days = self._infer_frequency_days(dates)
        base_amt = float(np.median(np.array(amts[-min(4, len(amts)) :], dtype=float)))
        base_amt = max(base_amt, 0.0)
        if base_amt <= 0.0:
            return []

        asof_date = asof.date()
        horizon_date = asof_date + timedelta(days=int(max(horizon_years, 0.25) * 365.25))
        last_date = dates[-1]

        projected: list[tuple[date, float]] = []
        next_date = last_date
        while next_date <= asof_date:
            next_date = next_date + timedelta(days=freq_days)

        while next_date <= horizon_date:
            projected.append((next_date, base_amt))
            next_date = next_date + timedelta(days=freq_days)

        return projected

    def projected_dividends(
        self,
        symbol: str,
        asof_ts: datetime,
        tau_years: float,
    ) -> list[DividendEvent]:
        if asof_ts.tzinfo is None:
            asof_ts = asof_ts.replace(tzinfo=timezone.utc)
        cache_key = symbol.upper()
        now = datetime.now(timezone.utc)
        cache = self._cache.get(cache_key)
        if cache is None or (now - cache[0]).total_seconds() > 6 * 3600:
            sched = self._project_schedule(cache_key, asof_ts, max(self.projection_horizon_years, tau_years + 0.25))
            self._cache[cache_key] = (now, sched)
        else:
            sched = cache[1]

        out: list[DividendEvent] = []
        horizon_sec = max(float(tau_years), 0.0) * 365.25 * 24.0 * 3600.0
        for ex_date, amt in sched:
            ex_dt = datetime(ex_date.year, ex_date.month, ex_date.day, 14, 0, 0, tzinfo=timezone.utc)
            t_sec = (ex_dt - asof_ts).total_seconds()
            if t_sec <= 0.0 or t_sec > horizon_sec:
                continue
            out.append(DividendEvent(amount=float(amt), time_to_ex_date=t_sec / (365.25 * 24.0 * 3600.0)))
        return out

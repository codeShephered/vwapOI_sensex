"""
parabolic_sar.py — Wilder Parabolic SAR (TradingView-aligned).

ROLE IN V2: EXIT MANAGEMENT ONLY — not used for entry confirmation.
  • Trailing stop in trending markets
  • PSAR exit when price crosses SAR during open position

Fix applied: constraint uses [Low(t-1), Low(t)] including current candle,
eliminating the 1-candle lag vs TradingView.
"""
from __future__ import annotations
from candle_builder import Candle
from logger_setup import get_module_logger
import config

logger = get_module_logger("SAR")


class ParabolicSAR:
    def __init__(self, start: float = None, increment: float = None, maximum: float = None):
        self._start = start     or config.SAR_START
        self._inc   = increment or config.SAR_INCREMENT
        self._max   = maximum   or config.SAR_MAX
        self._reset()

    def _reset(self) -> None:
        self._af      = self._start
        self._ep      = 0.0
        self._sar     = 0.0
        self._bullish = True
        self._ready   = False
        self._highs:  list[float] = []
        self._lows:   list[float] = []

    @property
    def value(self) -> float:
        return round(self._sar, 2)

    @property
    def is_bullish(self) -> bool:
        return self._bullish

    @property
    def is_bearish(self) -> bool:
        return not self._bullish

    @property
    def is_ready(self) -> bool:
        return self._ready

    @property
    def af(self) -> float:
        return round(self._af, 4)

    def update(self, candle: Candle) -> tuple[float | None, bool | None]:
        h, l = candle.high, candle.low

        # Append FIRST so constraint uses [Low(t-1), Low(t)] — TradingView-aligned
        self._highs.append(h)
        self._lows.append(l)
        if len(self._highs) > 3:
            self._highs.pop(0)
            self._lows.pop(0)

        if len(self._highs) < 2:
            return None, None

        if not self._ready:
            if candle.close >= self._highs[-2]:
                self._bullish = True
                self._sar = min(self._lows)
                self._ep  = max(self._highs)
            else:
                self._bullish = False
                self._sar = max(self._highs)
                self._ep  = min(self._lows)
            self._af    = self._start
            self._ready = True
            return round(self._sar, 2), self._bullish

        prev_sar = self._sar
        prev_ep  = self._ep
        prev_af  = self._af

        if self._bullish:
            new_sar = prev_sar + prev_af * (prev_ep - prev_sar)
            # Constraint: SAR <= min(Low(t-1), Low(t)) — current candle included
            if len(self._lows) >= 2:
                new_sar = min(new_sar, self._lows[-2], self._lows[-1])
            elif len(self._lows) == 1:
                new_sar = min(new_sar, self._lows[-1])

            if l < new_sar:
                self._bullish = False
                self._sar     = prev_ep
                self._ep      = l
                self._af      = self._start
                logger.debug(f"SAR ↓ BEARISH  sar={self._sar:.2f}")
            else:
                self._sar = new_sar
                if h > prev_ep:
                    self._ep = h
                    self._af = min(prev_af + self._inc, self._max)
        else:
            new_sar = prev_sar - prev_af * (prev_sar - prev_ep)
            # Constraint: SAR >= max(High(t-1), High(t)) — current candle included
            if len(self._highs) >= 2:
                new_sar = max(new_sar, self._highs[-2], self._highs[-1])
            elif len(self._highs) == 1:
                new_sar = max(new_sar, self._highs[-1])

            if h > new_sar:
                self._bullish = True
                self._sar     = prev_ep
                self._ep      = h
                self._af      = self._start
                logger.debug(f"SAR ↑ BULLISH  sar={self._sar:.2f}")
            else:
                self._sar = new_sar
                if l < prev_ep:
                    self._ep = l
                    self._af = min(prev_af + self._inc, self._max)

        logger.debug(
            f"SAR {'▲' if self._bullish else '▼'}  "
            f"sar={self._sar:.2f}  ep={self._ep:.2f}  af={self._af:.4f}"
        )
        return round(self._sar, 2), self._bullish

    def sl_hit(self, spot: float) -> bool:
        if not self._ready:
            return False
        return spot < self._sar if self._bullish else spot > self._sar

    def to_dict(self) -> dict:
        return {
            "value":   self.value,
            "bullish": self._bullish,
            "af":      self.af,
            "ep":      round(self._ep, 2),
            "ready":   self._ready,
        }

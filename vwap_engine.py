"""
vwap_engine.py — VWAP, slope, distance and market regime classification.

Market Regime:
  TRENDING_UP   : price > VWAP, VWAP slope positive, fast EMA > slow EMA
  TRENDING_DOWN : price < VWAP, VWAP slope negative, fast EMA < slow EMA
  SIDEWAYS      : price near VWAP, flat slope
  CHOPPY        : whipsawing around VWAP
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from candle_builder import Candle
import config


@dataclass
class VWAPState:
    vwap:          float = 0.0
    slope:         float = 0.0          # pts per candle (positive = up)
    distance_pct:  float = 0.0          # (price - vwap) / vwap * 100
    near_vwap:     bool  = False
    regime:        str   = "UNKNOWN"    # TRENDING_UP | TRENDING_DOWN | SIDEWAYS | CHOPPY
    price_above:   bool  = False
    ema_fast:      float = 0.0
    ema_slow:      float = 0.0


class VWAPEngine:
    """
    Computes VWAP intraday (resets each session) and classifies market regime.
    """

    def __init__(self, instrument: str) -> None:
        self.instrument = instrument
        self._reset()

    def _reset(self) -> None:
        self._cum_tp_vol = 0.0
        self._cum_vol    = 0.0
        self._vwap_history: list[float] = []
        self._close_history: list[float] = []
        self._ema_fast = 0.0
        self._ema_slow = 0.0
        self._ema_init = False

    def new_session(self) -> None:
        """Call at start of each trading day."""
        self._reset()

    def update(self, candle: Candle) -> VWAPState:
        """Feed completed 5-min candle; returns current VWAP state."""
        tp  = (candle.high + candle.low + candle.close) / 3.0
        vol = max(candle.volume, 1)

        self._cum_tp_vol += tp * vol
        self._cum_vol    += vol
        vwap = self._cum_tp_vol / self._cum_vol

        self._vwap_history.append(vwap)
        self._close_history.append(candle.close)

        # EMA fast/slow on close prices
        k_fast = 2 / (config.REGIME_FAST_EMA + 1)
        k_slow = 2 / (config.REGIME_SLOW_EMA + 1)
        if not self._ema_init:
            self._ema_fast = candle.close
            self._ema_slow = candle.close
            self._ema_init = True
        else:
            self._ema_fast = candle.close * k_fast + self._ema_fast * (1 - k_fast)
            self._ema_slow = candle.close * k_slow + self._ema_slow * (1 - k_slow)

        # VWAP slope over last N candles
        n = config.VWAP_SLOPE_LOOKBACK
        if len(self._vwap_history) >= n:
            slope = (self._vwap_history[-1] - self._vwap_history[-n]) / n
        else:
            slope = 0.0

        dist_pct     = (candle.close - vwap) / vwap * 100
        price_above  = candle.close > vwap
        near_vwap    = abs(dist_pct) <= config.VWAP_BAND_PCT

        # Regime classification
        regime = self._classify(price_above, slope, near_vwap)

        return VWAPState(
            vwap         = round(vwap, 2),
            slope        = round(slope, 4),
            distance_pct = round(dist_pct, 3),
            near_vwap    = near_vwap,
            regime       = regime,
            price_above  = price_above,
            ema_fast     = round(self._ema_fast, 2),
            ema_slow     = round(self._ema_slow, 2),
        )

    def _classify(self, price_above: bool, slope: float, near_vwap: bool) -> str:
        ema_bull = self._ema_fast > self._ema_slow
        slope_strong = abs(slope) > 1.0    # > 1 pt/candle = meaningful slope

        if price_above and ema_bull and slope > 0.5:
            return "TRENDING_UP"
        if not price_above and not ema_bull and slope < -0.5:
            return "TRENDING_DOWN"
        if near_vwap and not slope_strong:
            return "SIDEWAYS"
        return "CHOPPY"

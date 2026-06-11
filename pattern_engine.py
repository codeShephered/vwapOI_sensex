"""
pattern_engine.py — Candlestick patterns with tier scoring for V2.

Tier 1 (score=100): Three White Soldiers, Morning Doji Star, Three Black Crows, Evening Doji Star
Tier 2 (score=70) : Bullish Engulfing, Morning Star, Bearish Engulfing, Evening Star

Note: Hammer, Shooting Star, Piercing Line, Dark Cloud Cover removed per production.
"""
from __future__ import annotations
from typing import Optional, Tuple
from candle_builder import Candle
from logger_setup import get_module_logger

logger = get_module_logger("Patterns")

IMMEDIATE_EXIT_PATTERNS: frozenset = frozenset({
    "Morning Doji Star", "Evening Doji Star",
    "Three White Soldiers", "Three Black Crows",
    "Bullish Engulfing", "Bearish Engulfing",
})

TIER1_PATTERNS: frozenset = frozenset({
    "Three White Soldiers", "Morning Doji Star",
    "Three Black Crows", "Evening Doji Star",
})

TIER2_PATTERNS: frozenset = frozenset({
    "Bullish Engulfing", "Morning Star",
    "Bearish Engulfing", "Evening Star",
})

BULLISH_PATTERNS: frozenset = frozenset({
    "Morning Doji Star", "Three White Soldiers", "Bullish Engulfing", "Morning Star",
})
BEARISH_PATTERNS: frozenset = frozenset({
    "Evening Doji Star", "Three Black Crows", "Bearish Engulfing", "Evening Star",
})


def _body(c: Candle) -> float: return abs(c.close - c.open_price)
def _range(c: Candle) -> float: return c.high - c.low
def _upper_wick(c: Candle) -> float: return c.high - max(c.close, c.open_price)
def _lower_wick(c: Candle) -> float: return min(c.close, c.open_price) - c.low
def _is_doji(c: Candle, t: float = 0.1) -> bool:
    r = _range(c); return (_body(c)/r) <= t if r > 0 else True


def _morning_doji_star(c1, c2, c3) -> bool:
    c2bt = max(c2.open_price, c2.close)
    return (c1.is_bearish() and _body(c1) > _range(c1)*0.5
            and _is_doji(c2) and c2bt < c1.close
            and c3.is_bullish() and c3.open_price > c2bt
            and c3.close > (c1.open_price + c1.close)/2)


def _evening_doji_star(c1, c2, c3) -> bool:
    c2bt = max(c2.open_price, c2.close)
    c2bb = min(c2.open_price, c2.close)
    return (c1.is_bullish() and _body(c1) > _range(c1)*0.5
            and _is_doji(c2) and c2bb > c1.close
            and c3.is_bearish() and c3.open_price < c2bt
            and c3.close < (c1.open_price + c1.close)/2)


def _three_white_soldiers(c1, c2, c3) -> bool:
    return (c1.is_bullish() and c2.is_bullish() and c3.is_bullish()
            and c2.open_price >= c1.close*0.995 and c3.open_price >= c2.close*0.995
            and c2.close > c1.close and c3.close > c2.close
            and _body(c1) > _range(c1)*0.5
            and _body(c2) > _range(c2)*0.5
            and _body(c3) > _range(c3)*0.5)


def _three_black_crows(c1, c2, c3) -> bool:
    return (c1.is_bearish() and c2.is_bearish() and c3.is_bearish()
            and c2.open_price <= c1.close*1.005 and c3.open_price <= c2.close*1.005
            and c2.close < c1.close and c3.close < c2.close
            and _body(c1) > _range(c1)*0.5
            and _body(c2) > _range(c2)*0.5
            and _body(c3) > _range(c3)*0.5)


def _morning_star(c1, c2, c3, preceding=None) -> bool:
    if preceding and len(preceding) >= 3:
        in_dt = sum(1 for c in preceding if c.is_bearish()) >= len(preceding)*0.6
    else:
        in_dt = True
    c1bl = min(c1.open_price, c1.close)
    c1bm = (c1.open_price + c1.close) / 2
    c2bt = max(c2.open_price, c2.close)
    c2bb = min(c2.open_price, c2.close)
    gap_down = c2bt < c1bl
    tr2 = _range(c2) if _range(c2) > 0 else 1e-9
    c2_valid = (_body(c2) < _body(c1)*0.3 and _body(c2)/tr2 < 0.3
                and (c2.high - c2bt) > 0 and (c2bb - c2.low) > 0 and gap_down)
    gap_up = c3.open_price > c2bt
    c3_valid = (c3.is_bullish() and _body(c3) > _range(c3)*0.5
                and c3.close > c1bm and gap_up)
    return in_dt and c1.is_bearish() and _body(c1) > _range(c1)*0.5 and c2_valid and c3_valid


def _evening_star(c1, c2, c3) -> bool:
    return (c1.is_bullish() and _body(c1) > _range(c1)*0.5
            and _body(c2) < _body(c1)*0.3 and c3.is_bearish()
            and c3.close < (c1.open_price + c1.close)/2)


def _bullish_engulfing(c1, c2) -> bool:
    return (c1.is_bearish() and c2.is_bullish()
            and c2.open_price <= c1.close and c2.close >= c1.open_price
            and _body(c2) > _body(c1))


def _bearish_engulfing(c1, c2) -> bool:
    return (c1.is_bullish() and c2.is_bearish()
            and c2.open_price >= c1.close and c2.close <= c1.open_price
            and _body(c2) > _body(c1))


class PatternEngine:

    def scan(self, candles: list[Candle]) -> Tuple[Optional[str], str]:
        n = len(candles)
        if n == 0:
            return None, "none"

        if n >= 3:
            c1, c2, c3 = candles[-3], candles[-2], candles[-1]
            preceding  = candles[:-3] if len(candles) > 3 else []

            if _morning_doji_star(c1, c2, c3):
                logger.info("▲ Morning Doji Star (Tier1) — BULLISH")
                return "Morning Doji Star", "bullish"
            if _three_white_soldiers(c1, c2, c3):
                logger.info("▲ Three White Soldiers (Tier1) — BULLISH")
                return "Three White Soldiers", "bullish"
            if _morning_star(c1, c2, c3, preceding):
                logger.info("▲ Morning Star (Tier2) — BULLISH")
                return "Morning Star", "bullish"
            if _evening_doji_star(c1, c2, c3):
                logger.info("▼ Evening Doji Star (Tier1) — BEARISH")
                return "Evening Doji Star", "bearish"
            if _three_black_crows(c1, c2, c3):
                logger.info("▼ Three Black Crows (Tier1) — BEARISH")
                return "Three Black Crows", "bearish"
            if _evening_star(c1, c2, c3):
                logger.info("▼ Evening Star (Tier2) — BEARISH")
                return "Evening Star", "bearish"

        if n >= 2:
            c1, c2 = candles[-2], candles[-1]
            if _bullish_engulfing(c1, c2):
                logger.info("▲ Bullish Engulfing (Tier2) — BULLISH")
                return "Bullish Engulfing", "bullish"
            if _bearish_engulfing(c1, c2):
                logger.info("▼ Bearish Engulfing (Tier2) — BEARISH")
                return "Bearish Engulfing", "bearish"

        return None, "none"

    def get_tier(self, pattern: str) -> int:
        if pattern in TIER1_PATTERNS: return 1
        if pattern in TIER2_PATTERNS: return 2
        return 0

    def is_reversal_of(self, candles: list[Candle], open_direction: str) -> Tuple[Optional[str], str]:
        pat, direction = self.scan(candles)
        if not pat:
            return None, "none"
        if open_direction == "bullish" and direction == "bearish":
            return pat, direction
        if open_direction == "bearish" and direction == "bullish":
            return pat, direction
        return None, "none"

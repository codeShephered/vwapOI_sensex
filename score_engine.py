"""
score_engine.py — Trade scoring engine.

Score = pattern_base + vwap_bonus + oi_bonus + near_vwap_bonus + volume_bonus
Only trades scoring >= config.MIN_TRADE_SCORE are taken.

Example score for Tier1 + VWAP aligned + OI confirmed + near VWAP:
  100 + 20 + 20 + 20 = 160  → trade taken
"""
from __future__ import annotations
from dataclasses import dataclass
from vwap_engine import VWAPState
from oi_engine   import OIState, OI_BULLISH, OI_BEARISH, OI_NEUTRAL
from pattern_engine import TIER1_PATTERNS, TIER2_PATTERNS
import config


@dataclass
class ScoreResult:
    total:             int   = 0
    passes:            bool  = False
    pattern_score:     int   = 0
    vwap_score:        int   = 0
    oi_score:          int   = 0
    near_vwap_score:   int   = 0
    volume_score:      int   = 0
    breakdown:         str   = ""
    tier:              int   = 0
    lot_multiplier:    float = 1.0     # 1.0 = full lot, 0.6 = partial (Tier2)


def compute_score(
    pattern:   str,
    direction: str,
    vwap:      VWAPState,
    oi:        OIState,
    candle_volume: int = 0,
    avg_volume: float  = 0.0,
) -> ScoreResult:
    """
    Compute trade score for a given pattern + market context.
    """
    res = ScoreResult()

    # ── Pattern base score ────────────────────────────────────────────────────
    if pattern in TIER1_PATTERNS:
        res.pattern_score = config.SCORE_TIER1_PATTERN
        res.tier          = 1
        res.lot_multiplier = 1.0
    elif pattern in TIER2_PATTERNS:
        res.pattern_score = config.SCORE_TIER2_PATTERN
        res.tier          = 2
        res.lot_multiplier = 1.0   # kept as 1 lot; can reduce to 0.6 if desired
    else:
        res.breakdown = f"Pattern '{pattern}' not recognised"
        return res

    # ── VWAP alignment ────────────────────────────────────────────────────────
    if direction == "bullish" and vwap.price_above:
        res.vwap_score = config.SCORE_VWAP_ALIGNED
    elif direction == "bearish" and not vwap.price_above:
        res.vwap_score = config.SCORE_VWAP_ALIGNED

    # ── OI confirmation ───────────────────────────────────────────────────────
    if direction == "bullish" and oi.signal == OI_BULLISH:
        res.oi_score = config.SCORE_OI_CONFIRMED
    elif direction == "bearish" and oi.signal == OI_BEARISH:
        res.oi_score = config.SCORE_OI_CONFIRMED

    # ── Near VWAP ─────────────────────────────────────────────────────────────
    if vwap.near_vwap:
        res.near_vwap_score = config.SCORE_NEAR_VWAP

    # ── Volume confirmation ───────────────────────────────────────────────────
    if avg_volume > 0 and candle_volume >= avg_volume * 1.2:
        res.volume_score = config.SCORE_VOLUME_CONFIRM

    res.total  = (res.pattern_score + res.vwap_score + res.oi_score
                  + res.near_vwap_score + res.volume_score)
    res.passes = res.total >= config.MIN_TRADE_SCORE
    res.breakdown = (
        f"Pattern({res.pattern_score})+VWAP({res.vwap_score})"
        f"+OI({res.oi_score})+NearVWAP({res.near_vwap_score})"
        f"+Vol({res.volume_score}) = {res.total}"
        f" {'✓ TRADE' if res.passes else '✗ SKIP'}"
    )
    return res

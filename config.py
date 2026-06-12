"""
config.py — Central configuration for NSE Options Trading System V2.

Strategy:
  Entry  : VWAP + Option Chain OI Analysis + Candlestick Patterns + Market Regime
  Exit   : Premium SL (15-20%) | Breakeven at +25% | Trailing at +40% | PSAR exit | 3:15 PM
  NOTE   : PSAR used for EXIT management only — NOT for entry confirmation
"""
import os

# ── Zerodha ───────────────────────────────────────────────────────────────────
ZERODHA_API_KEY      = ""   # permanent key from kite.zerodha.com
ZERODHA_API_SECRET   = ""   # permanent secret
ZERODHA_ACCESS_TOKEN = ""   # regenerate every morning (expires midnight IST)

# ── Mode ──────────────────────────────────────────────────────────────────────
TRADING_MODE = "paper"   # "paper" | "live"

# ── WebSocket / polling ───────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS    = 30
CANDLE_TIMEFRAME_MINUTES = 5

# ── Market timing ─────────────────────────────────────────────────────────────
NO_NEW_TRADE_HOUR    = 14
NO_NEW_TRADE_MINUTE  = 45   # No new positions after 2:45 PM
SQUARE_OFF_HOUR      = 15
SQUARE_OFF_MINUTE    = 15   # Force close all at 3:15 PM
ENTRY_START_HOUR     = 9
ENTRY_START_MINUTE   = 30   # No new positions before 9:30 AM

# ── Instruments ───────────────────────────────────────────────────────────────
INSTRUMENTS = {
    "NIFTY": {
        "zerodha_symbol":    "NSE:NIFTY 50",
        "lot_size":          65,
        "strike_interval":   50,
        "expiry_type":       "weekly",
        "expiry_weekday":    1,          # Tuesday
        "ce_strike_mode":    "otm",
        "pe_strike_mode":    "otm",
        "vwap_token":        256265,     # Zerodha token for NIFTY 50
        "exchange":          "NSE",
        "options_exchange":  "NFO",
    },
    "BANKNIFTY": {
        "zerodha_symbol":    "NSE:NIFTY BANK",
        "lot_size":          30,
        "strike_interval":   100,
        "expiry_type":       "monthly",
        "expiry_weekday":    1,          # Tuesday
        "ce_strike_mode":    "otm",
        "pe_strike_mode":    "otm",
        "vwap_token":        260105,     # Zerodha token for BANKNIFTY
        "exchange":          "NSE",
        "options_exchange":  "NFO",
    },
    "SENSEX": {
        "zerodha_symbol":    "BSE:SENSEX",
        "lot_size":          20,         # BSE SENSEX lot size
        "strike_interval":   100,
        "expiry_type":       "weekly",
        "expiry_weekday":    3,          # Thursday
        "ce_strike_mode":    "otm",
        "pe_strike_mode":    "otm",
        "vwap_token":        265,        # Zerodha BSE token for SENSEX
        "exchange":          "BSE",
        "options_exchange":  "BFO",      # BSE Futures & Options
    },
}

# ── Parabolic SAR (exit management only) ─────────────────────────────────────
SAR_START        = 0.02
SAR_INCREMENT    = 0.02
SAR_MAX          = 0.20
SAR_SEED_CANDLES = 5

# ── VWAP ─────────────────────────────────────────────────────────────────────
VWAP_SLOPE_LOOKBACK  = 5    # candles to compute VWAP slope
VWAP_BAND_PCT        = 0.3  # % distance from VWAP to consider "near VWAP"

# ── Option Chain OI ───────────────────────────────────────────────────────────
OI_CHANGE_PCT_THRESHOLD = 5.0   # minimum % OI change to count as significant
OI_STRIKES_EACH_SIDE    = 5     # number of strikes either side of ATM to scan

# ── Trade Scoring ─────────────────────────────────────────────────────────────
# Pattern base scores
SCORE_TIER1_PATTERN  = 100   # Three White Soldiers, Morning Doji Star, Three Black Crows, Evening Doji Star
SCORE_TIER2_PATTERN  = 70    # Bullish Engulfing, Morning Star, Bearish Engulfing, Evening Star

# Bonus scores
SCORE_VWAP_ALIGNED   = 20
SCORE_OI_CONFIRMED   = 20
SCORE_NEAR_VWAP      = 20
SCORE_VOLUME_CONFIRM = 10

# Minimum score to take a trade
MIN_TRADE_SCORE      = 90

# ── Position sizing ───────────────────────────────────────────────────────────
# Tier 1 patterns: 100% of planned 1 lot | Tier 2 patterns: 60%
# In this system we keep it simple — always 1 lot (configurable)
TIER1_LOTS = 1
TIER2_LOTS = 1   # set to 0.6 conceptually; actual lot count must be integer

# ── Risk management ───────────────────────────────────────────────────────────
PREMIUM_SL_PCT        = 0.20   # Initial SL: 20% drop from entry premium (configurable 15-20%)
BREAKEVEN_TRIGGER_PCT = 0.25   # Move SL to breakeven when profit ≥ +25%
TRAIL_TRIGGER_PCT     = 0.40   # Activate trailing when profit ≥ +40%
TRAIL_RETRACE_PCT     = 0.15   # Exit when premium retraces 15% from highest

# ── Market regime ─────────────────────────────────────────────────────────────
# EMA lengths for regime classification
REGIME_FAST_EMA  = 9
REGIME_SLOW_EMA  = 21
REGIME_ADX_LEN   = 14
REGIME_ADX_STRONG = 25   # ADX > 25 = trending; below = sideways/choppy

# ── Implied volatility (Black-Scholes fallback) ───────────────────────────────
OPTION_VOLATILITY = {"NIFTY": 0.14, "BANKNIFTY": 0.17, "SENSEX": 0.14}

# ── Logging ───────────────────────────────────────────────────────────────────
LOG_FILE        = "trading.log"
LOG_LEVEL       = "DEBUG"
MAX_MEMORY_LOGS = 1000

# ── Flask ─────────────────────────────────────────────────────────────────────
FLASK_HOST  = "0.0.0.0"
FLASK_PORT  = 5004
SECRET_KEY  = "nse_options_v2_2026"

"""
trade_engine.py — Position lifecycle V2.

Exit hierarchy:
  1. Initial SL:    option premium drops >= PREMIUM_SL_PCT (15-20%) — every tick
  2. Breakeven:     at +25% profit → move SL to entry premium (no loss)
  3. Trailing:      at +40% profit → trail 15% from highest premium achieved
  4. PSAR exit:     when SAR crosses price (trending markets only, exit management)
  5. Pattern exit:  SAR reversal or Tier1 pattern on candle close
  6. 3:15 PM:       force close all

Note: PSAR is NOT used for entry — only for exit protection in trending markets.
"""
from __future__ import annotations
import threading, math
from datetime import datetime, date
from logger_setup import get_module_logger
from data_feed import get_nifty_expiry, get_banknifty_expiry, get_expiry, build_symbol, select_strike, bs_estimate
from pattern_engine import IMMEDIATE_EXIT_PATTERNS
import config

logger = get_module_logger("TradeEngine")


class Position:
    def __init__(self, instrument: str, direction: str, option_type: str,
                 strike: float, expiry: date, entry_premium: float,
                 pattern: str, lot_size: int, score: int = 0,
                 is_estimated: bool = False):
        self.instrument    = instrument
        self.direction     = direction
        self.option_type   = option_type
        self.strike        = strike
        self.expiry        = expiry
        self.expiry_str    = expiry.strftime("%d-%b-%Y")
        self.entry_premium = max(entry_premium, 0.05)
        self.current_premium  = self.entry_premium
        self.highest_premium  = self.entry_premium   # for trailing
        self.is_estimated     = is_estimated
        self.pnl              = 0.0
        self.score            = score
        self.pattern          = pattern
        self.lot_size         = lot_size
        self.entry_time       = datetime.now()
        self.is_open          = True
        self.pending_reversal = ""
        self._stale_ticks     = 0

        # Risk management state
        self.sl_premium       = self.entry_premium * (1 - config.PREMIUM_SL_PCT)
        self.breakeven_hit    = False
        self.trailing_active  = False

        self.zerodha_symbol = build_symbol(instrument, expiry, strike, option_type)

    def update_premium(self, ltp: float, is_estimated: bool = False) -> None:
        if ltp > 0:
            self.current_premium = ltp
            self.is_estimated    = is_estimated
            self.pnl             = (ltp - self.entry_premium) * self.lot_size
            self._stale_ticks    = 0
            # Track highest premium for trailing
            if ltp > self.highest_premium:
                self.highest_premium = ltp
            # Update SL stages
            self._update_sl_stages(ltp)
        else:
            self._stale_ticks += 1
            if self._stale_ticks == 3:
                logger.warning(
                    f"{self.instrument} {self.option_type} {int(self.strike)}: "
                    f"LTP unavailable — last known ₹{self.current_premium:.2f}"
                )

    def _update_sl_stages(self, ltp: float) -> None:
        profit_pct = (ltp - self.entry_premium) / self.entry_premium

        # Stage 1: breakeven at +25%
        if not self.breakeven_hit and profit_pct >= config.BREAKEVEN_TRIGGER_PCT:
            self.sl_premium    = self.entry_premium   # move SL to entry = no loss
            self.breakeven_hit = True
            logger.info(
                f"{self.instrument} {self.option_type}: breakeven SL activated "
                f"(profit={profit_pct*100:.1f}%  SL→entry ₹{self.entry_premium:.2f})"
            )

        # Stage 2: trailing at +40%
        if profit_pct >= config.TRAIL_TRIGGER_PCT:
            self.trailing_active = True
            trail_sl = self.highest_premium * (1 - config.TRAIL_RETRACE_PCT)
            if trail_sl > self.sl_premium:
                self.sl_premium = trail_sl
                logger.debug(
                    f"{self.instrument} trailing SL → ₹{self.sl_premium:.2f} "
                    f"(highest=₹{self.highest_premium:.2f})"
                )

    def premium_sl_hit(self) -> bool:
        return self.current_premium <= self.sl_premium

    def sar_sl_hit(self, spot: float, sar_val: float) -> bool:
        """PSAR exit — for trending market exit management only."""
        return spot < sar_val if self.option_type == "CE" else spot > sar_val

    def to_dict(self) -> dict:
        pct = ((self.current_premium - self.entry_premium) / self.entry_premium * 100
               if self.entry_premium else 0)
        return {
            "instrument":      self.instrument,
            "direction":       self.direction,
            "option_type":     self.option_type,
            "strike":          self.strike,
            "expiry":          self.expiry_str,
            "pattern":         self.pattern,
            "score":           self.score,
            "entry_premium":   round(self.entry_premium, 2),
            "current_premium": round(self.current_premium, 2),
            "highest_premium": round(self.highest_premium, 2),
            "sl_premium":      round(self.sl_premium, 2),
            "is_estimated":    self.is_estimated,
            "pnl":             round(self.pnl, 2),
            "pnl_pct":         round(pct, 2),
            "entry_time":      self.entry_time.strftime("%H:%M:%S"),
            "zerodha_symbol":  self.zerodha_symbol,
            "breakeven_hit":   self.breakeven_hit,
            "trailing_active": self.trailing_active,
            "lot_size":        self.lot_size,
        }


class TradeEngine:
    def __init__(self):
        self._positions: dict[str, Position] = {}
        self._history:   list[dict]          = []
        self._lock       = threading.Lock()
        self.stats       = {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}

    # ── Queries ───────────────────────────────────────────────────────────────

    def has_open(self, instrument: str) -> bool:
        with self._lock:
            p = self._positions.get(instrument)
            return p is not None and p.is_open

    def get_pos(self, instrument: str) -> Position | None:
        with self._lock:
            return self._positions.get(instrument)

    def all_positions(self) -> list[dict]:
        with self._lock:
            return [p.to_dict() for p in self._positions.values() if p.is_open]

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._history)

    def get_stats(self) -> dict:
        with self._lock:
            s = self.stats.copy()
            s["win_rate"] = round(s["wins"] / s["total"] * 100, 1) if s["total"] else 0.0
            return s

    # ── Timing ────────────────────────────────────────────────────────────────

    @staticmethod
    def can_enter() -> bool:
        now = datetime.now()
        if now.weekday() >= 5:
            return False
        h, m = now.hour, now.minute
        after_open  = (h > config.ENTRY_START_HOUR or
                       (h == config.ENTRY_START_HOUR and m >= config.ENTRY_START_MINUTE))
        before_close = (h < config.NO_NEW_TRADE_HOUR or
                        (h == config.NO_NEW_TRADE_HOUR and m < config.NO_NEW_TRADE_MINUTE))
        return after_open and before_close

    @staticmethod
    def must_square_off() -> bool:
        now = datetime.now()
        return now.hour > config.SQUARE_OFF_HOUR or (
            now.hour == config.SQUARE_OFF_HOUR and
            now.minute >= config.SQUARE_OFF_MINUTE
        )

    # ── Build signal ──────────────────────────────────────────────────────────

    def build_signal(self, instrument: str, direction: str, spot: float,
                     pattern: str, score: int = 0) -> dict:
        cfg      = config.INSTRUMENTS[instrument]
        opt_type = "CE" if direction == "bullish" else "PE"
        strike   = select_strike(instrument, spot, opt_type)
        expiry   = get_expiry(instrument)
        symbol   = build_symbol(instrument, expiry, strike, opt_type)
        logger.info(
            f"SIGNAL  {instrument} {opt_type} {int(strike)}  "
            f"expiry={expiry.strftime('%d-%b-%Y')}  "
            f"pattern={pattern}  spot={spot:.2f}  score={score}"
        )
        return {
            "instrument":    instrument,
            "direction":     direction,
            "option_type":   opt_type,
            "strike":        strike,
            "expiry":        expiry,
            "expiry_str":    expiry.strftime("%d-%b-%Y"),
            "pattern":       pattern,
            "spot":          spot,
            "score":         score,
            "lot_size":      cfg["lot_size"],
            "zerodha_symbol": symbol,
        }

    # ── Entry ─────────────────────────────────────────────────────────────────

    def enter(self, signal: dict, ltp: float) -> Position:
        is_est = ltp == 0
        if is_est:
            ltp = bs_estimate(signal["instrument"], signal["spot"],
                              signal["strike"], signal["option_type"],
                              max((signal["expiry"] - date.today()).days, 1))
            logger.warning(f"{signal['instrument']} {signal['option_type']}: "
                           f"BS estimate ₹{ltp:.2f}")
        else:
            logger.info(f"{signal['instrument']} {signal['option_type']}: "
                        f"entry LTP = ₹{ltp:.2f} (real)")

        with self._lock:
            pos = Position(
                instrument    = signal["instrument"],
                direction     = signal["direction"],
                option_type   = signal["option_type"],
                strike        = signal["strike"],
                expiry        = signal["expiry"],
                entry_premium = ltp,
                pattern       = signal["pattern"],
                lot_size      = signal["lot_size"],
                score         = signal.get("score", 0),
                is_estimated  = is_est,
            )
            self._positions[signal["instrument"]] = pos
            self.stats["total"] += 1
        return pos

    def place_live_order(self, signal: dict, feed) -> None:
        from kiteconnect import KiteConnect
        feed.place_order(signal["zerodha_symbol"], signal["lot_size"],
                         KiteConnect.TRANSACTION_TYPE_BUY)

    # ── Tick exits (every ~100ms via WebSocket) ───────────────────────────────

    def check_tick(self, instrument: str, spot: float, ltp: float,
                   sar_val: float = 0.0, regime: str = "") -> dict | None:
        """
        Called on every WebSocket tick.
        Checks: premium SL | PSAR exit (trending regime only).
        """
        with self._lock:
            pos = self._positions.get(instrument)
            if not pos or not pos.is_open:
                return None
            pos.update_premium(ltp)

            # Premium SL (initial 20%, breakeven, or trailing floor)
            if pos.premium_sl_hit():
                stage = ("trailing" if pos.trailing_active
                         else "breakeven" if pos.breakeven_hit
                         else "initial")
                return self._close(pos, instrument,
                                   f"Premium SL ({stage})  "
                                   f"current=₹{pos.current_premium:.2f}  "
                                   f"floor=₹{pos.sl_premium:.2f}")

            # PSAR exit — only in trending markets, exit management
            if sar_val > 0 and regime in ("TRENDING_UP", "TRENDING_DOWN"):
                if pos.sar_sl_hit(spot, sar_val):
                    return self._close(pos, instrument,
                                       f"PSAR exit (trend)  spot={spot:.2f}  sar={sar_val:.2f}")
        return None

    # ── Candle-close exits ────────────────────────────────────────────────────

    def check_candle(self, instrument: str, ltp: float,
                     sar_value: float, sar_reversed: bool,
                     rev_pattern: str = "") -> dict | None:
        """
        Called on each 5-min candle close.
        SAR reversal exit | reversal pattern exit.
        """
        with self._lock:
            pos = self._positions.get(instrument)
            if not pos or not pos.is_open:
                return None
            pos.update_premium(ltp)

            if sar_reversed:
                return self._close(pos, instrument,
                                   f"SAR reversed  new_sar={sar_value:.2f}")

            if rev_pattern and rev_pattern in IMMEDIATE_EXIT_PATTERNS:
                return self._close(pos, instrument,
                                   f"Reversal pattern: {rev_pattern}")
        return None

    # ── Close ─────────────────────────────────────────────────────────────────

    def _close(self, pos: Position, instrument: str, reason: str) -> dict:
        pos.is_open = False
        pnl = pos.pnl
        if pnl > 0:   self.stats["wins"]   += 1
        elif pnl < 0: self.stats["losses"] += 1
        self.stats["total_pnl"] += pnl
        record = {
            **pos.to_dict(),
            "exit_reason": reason,
            "exit_time":   datetime.now().strftime("%H:%M:%S"),
            "final_pnl":   round(pnl, 2),
        }
        self._history.append(record)
        del self._positions[instrument]
        sign = "+" if pnl >= 0 else ""
        logger.info(f"CLOSED  {pos.instrument} {pos.option_type} {int(pos.strike)}  "
                    f"reason={reason}  PnL={sign}₹{pnl:.2f}")
        return record

    def close_position(self, instrument: str, reason: str = "Manual exit") -> dict | None:
        with self._lock:
            pos = self._positions.get(instrument)
            if not pos or not pos.is_open:
                return None
            return self._close(pos, instrument, reason)

    def force_close_all(self, reason: str = "3:15 PM square-off") -> list[dict]:
        closed = []
        with self._lock:
            keys = list(self._positions.keys())
        for k in keys:
            with self._lock:
                pos = self._positions.get(k)
                if not pos or not pos.is_open:
                    continue
                r = self._close(pos, k, reason)
                closed.append(r)
        return closed

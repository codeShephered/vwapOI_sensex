"""
oi_engine.py — Option Chain OI analysis.

Bullish signal  : Put OI Addition + Call OI Unwinding
Bearish signal  : Call OI Addition + Put OI Unwinding
Neutral/unclear : mixed signals

Data source: Zerodha quote() on NFO strikes around ATM.
Fallback    : returns OI_NEUTRAL when Zerodha is unavailable.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Optional
from logger_setup import get_module_logger
import config

logger = get_module_logger("OI")

OI_BULLISH = "BULLISH"
OI_BEARISH = "BEARISH"
OI_NEUTRAL = "NEUTRAL"


@dataclass
class OIState:
    signal:          str   = OI_NEUTRAL    # BULLISH | BEARISH | NEUTRAL
    call_oi_change:  float = 0.0           # +ve = addition, -ve = unwinding
    put_oi_change:   float = 0.0
    pcr:             float = 1.0           # Put/Call OI ratio
    support_strike:  float = 0.0          # highest put OI strike = support
    resist_strike:   float = 0.0          # highest call OI strike = resistance
    confidence:      int   = 0            # 0-100


class OIEngine:
    """Analyses option chain OI to confirm entry direction."""

    def __init__(self, instrument: str) -> None:
        self.instrument = instrument
        self._prev_oi:  dict[str, dict] = {}   # {"symbol": {"call_oi": x, "put_oi": x}}

    def analyse(self, spot: float, kite=None) -> OIState:
        """
        Fetch OI for strikes around ATM and compute direction signal.
        Returns OI_NEUTRAL if kite is None or data unavailable.
        """
        if kite is None:
            return OIState()

        cfg      = config.INSTRUMENTS[self.instrument]
        interval = cfg["strike_interval"]
        atm      = round(spot / interval) * interval
        n        = config.OI_STRIKES_EACH_SIDE

        strikes  = [atm + (i * interval) for i in range(-n, n + 1)]
        threshold = config.OI_CHANGE_PCT_THRESHOLD

        call_add  = 0; call_unwind = 0
        put_add   = 0; put_unwind  = 0
        total_ce_oi = 0; total_pe_oi = 0
        max_ce_oi = 0; max_pe_oi = 0
        resist_strike = atm; support_strike = atm

        from data_feed import build_symbol
        from datetime import date as _date
        try:
            from data_feed import get_nifty_expiry, get_banknifty_expiry
            expiry = (get_nifty_expiry() if cfg["expiry_type"] == "weekly"
                      else get_banknifty_expiry())
        except Exception:
            return OIState()

        symbols = []
        for s in strikes:
            symbols.append(f"NFO:{build_symbol(self.instrument, expiry, s, 'CE')}")
            symbols.append(f"NFO:{build_symbol(self.instrument, expiry, s, 'PE')}")

        try:
            raw = kite.quote(symbols)
        except Exception as exc:
            logger.debug(f"OI fetch failed: {exc}")
            return OIState()

        for sym, data in raw.items():
            oi_now = data.get("oi", 0) or 0
            prev   = self._prev_oi.get(sym, {}).get("oi", oi_now)
            change_pct = (oi_now - prev) / prev * 100 if prev else 0

            is_ce = sym.endswith("CE")
            is_pe = sym.endswith("PE")

            if is_ce:
                total_ce_oi += oi_now
                if oi_now > max_ce_oi:
                    max_ce_oi = oi_now
                    try:
                        # extract strike from symbol name
                        resist_strike = float(''.join(filter(str.isdigit, sym.split(':')[1].replace(self.instrument, '').replace('CE','').replace('PE','')[-6:])))
                    except Exception:
                        pass
                if change_pct > threshold:
                    call_add += 1
                elif change_pct < -threshold:
                    call_unwind += 1

            if is_pe:
                total_pe_oi += oi_now
                if oi_now > max_pe_oi:
                    max_pe_oi = oi_now
                    try:
                        support_strike = float(''.join(filter(str.isdigit, sym.split(':')[1].replace(self.instrument, '').replace('CE','').replace('PE','')[-6:])))
                    except Exception:
                        pass
                if change_pct > threshold:
                    put_add += 1
                elif change_pct < -threshold:
                    put_unwind += 1

            self._prev_oi[sym] = {"oi": oi_now}

        pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 1.0

        # Signal logic
        if put_add > 0 and call_unwind > 0:
            signal = OI_BULLISH
            confidence = min(100, (put_add + call_unwind) * 15)
        elif call_add > 0 and put_unwind > 0:
            signal = OI_BEARISH
            confidence = min(100, (call_add + put_unwind) * 15)
        else:
            signal = OI_NEUTRAL
            confidence = 0

        return OIState(
            signal         = signal,
            call_oi_change = float(call_add - call_unwind),
            put_oi_change  = float(put_add - put_unwind),
            pcr            = round(pcr, 3),
            support_strike = support_strike,
            resist_strike  = resist_strike,
            confidence     = confidence,
        )

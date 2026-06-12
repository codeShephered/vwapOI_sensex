"""
data_feed.py — Zerodha KiteConnect data + option contract utilities.

Contract selection (constraint 8):
  NIFTY    : current weekly contract (Thursday expiry).
             If today IS Thursday (expiry day), use next Thursday.
  BANKNIFTY: monthly contract only (last Wednesday of the month).
             If today is past that date, use next month's last Wednesday.

Symbol formats (Zerodha NFO):
  Weekly  : NIFTY{YY}{M}{DD}{STRIKE}{TYPE}    e.g. NIFTY2651424300CE
  Monthly : BANKNIFTY{YY}{MMM}{STRIKE}{TYPE}  e.g. BANKNIFTY26MAY55800CE
"""
from __future__ import annotations
import math
from datetime import date, timedelta
from logger_setup import get_module_logger
import config

logger = get_module_logger("Feed")

# One-character month codes for weekly symbols
_MONTH_CHAR = {1:"1",2:"2",3:"3",4:"4",5:"5",6:"6",
               7:"7",8:"8",9:"9",10:"O",11:"N",12:"D"}
# Three-letter month names for monthly symbols
_MONTH_NAME = {1:"JAN",2:"FEB",3:"MAR",4:"APR",5:"MAY",6:"JUN",
               7:"JUL",8:"AUG",9:"SEP",10:"OCT",11:"NOV",12:"DEC"}


# ── Expiry helpers ─────────────────────────────────────────────────────────────
'''
def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Last occurrence of `weekday` (0=Mon … 6=Sun) in the given month."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def get_nifty_expiry() -> date:
    """
    Current NIFTY weekly expiry (Thursday).
    If today is Thursday (expiry day itself), return next Thursday.
    """
    today = date.today()
    delta = (3 - today.weekday()) % 7   # 3 = Thursday
    if delta == 0:
        delta = 7
    return today + timedelta(days=delta)


def get_banknifty_expiry() -> date:
    """
    BANKNIFTY monthly expiry = last Wednesday of the current month.
    If today is past that date, return next month's last Wednesday.
    """
    today = date.today()
    exp   = _last_weekday_of_month(today.year, today.month, 2)
    if today >= exp:
        if today.month == 12:
            exp = _last_weekday_of_month(today.year + 1, 1, 2)
        else:
            exp = _last_weekday_of_month(today.year, today.month + 1, 2)
    return exp
'''
_NSE_HOLIDAYS: frozenset[date] = frozenset({
    date(2026, 10, 20),   # Tuesday — Diwali Laxmi Puja
    date(2026, 11, 10),   # Tuesday — Gurunanak Jayanti
    date(2026, 11, 24),   # Tuesday — add/remove as NSE calendar changes
})


def _adjust_for_holiday(expiry: date) -> date:
    """
    If the computed expiry Tuesday is an NSE holiday,
    return the preceding Monday instead.
    Keeps stepping back by one day until a non-holiday weekday is found
    (handles the rare case where Monday is also a holiday).
    """
    while expiry in _NSE_HOLIDAYS:
        expiry -= timedelta(days=1)   # step back to Monday (or earlier if needed)
    return expiry


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Last occurrence of `weekday` (0=Mon … 6=Sun) in the given month."""
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def get_nifty_expiry() -> date:
    """
    Current NIFTY weekly expiry (Tuesday).
    If today IS Tuesday (expiry day itself), use next Tuesday.
    If the Tuesday is an NSE holiday, use the preceding Monday.
    """
    today = date.today()
    delta = (1 - today.weekday()) % 7   # 1 = Tuesday
    if delta == 0:
        delta = 7                        # today is Tuesday → jump to next week
    expiry = today + timedelta(days=delta)
    return _adjust_for_holiday(expiry)


def get_banknifty_expiry() -> date:
    """
    BANKNIFTY monthly expiry = last Tuesday of the current month.
    If today is past that date, use next month's last Tuesday.
    If that Tuesday is an NSE holiday, use the preceding Monday.
    """
    today = date.today()
    exp   = _last_weekday_of_month(today.year, today.month, 1)   # 1 = Tuesday
    if today >= exp:
        if today.month == 12:
            exp = _last_weekday_of_month(today.year + 1, 1, 1)
        else:
            exp = _last_weekday_of_month(today.year, today.month + 1, 1)
    return _adjust_for_holiday(exp)


def get_sensex_expiry() -> date:
    """
    SENSEX weekly expiry = current Friday (BSE).
    If today IS Friday (expiry day), use next Friday.
    """
    today = date.today()
    delta = (4 - today.weekday()) % 7   # 4 = Friday
    if delta == 0:
        delta = 7
    return today + timedelta(days=delta)


def get_expiry(instrument: str) -> date:
    """Universal expiry getter — routes to the correct function per instrument."""
    cfg = config.INSTRUMENTS.get(instrument, {})
    if cfg.get("expiry_type") == "monthly":
        return get_banknifty_expiry()
    if instrument == "SENSEX":
        return get_sensex_expiry()
    return get_nifty_expiry()


def build_symbol(instrument: str, expiry: date,
                 strike: float, option_type: str) -> str:
    """
    Build the BFO/NFO trading symbol for Zerodha.

    NIFTY weekly:      NIFTY{YY}{M}{DD}{STRIKE}CE/PE   (NFO)
    BANKNIFTY monthly: BANKNIFTY{YY}{MMM}{STRIKE}CE/PE  (NFO)
    SENSEX weekly:     SENSEX{YY}{M}{DD}{STRIKE}CE/PE   (BFO)
    """
    yy    = str(expiry.year)[2:]
    cfg   = config.INSTRUMENTS.get(instrument, {})
    etype = cfg.get("expiry_type", "weekly")

    if etype == "monthly":
        mon = _MONTH_NAME[expiry.month]
        return f"{instrument}{yy}{mon}{int(strike)}{option_type}"
    else:
        m  = _MONTH_CHAR[expiry.month]
        dd = f"{expiry.day:02d}"
        return f"{instrument}{yy}{m}{dd}{int(strike)}{option_type}"


def select_strike(instrument: str, spot: float, option_type: str) -> float:
    """Choose ITM or ATM strike depending on instrument config."""
    cfg      = config.INSTRUMENTS[instrument]
    interval = cfg["strike_interval"]
    mode     = cfg.get(f"{'ce' if option_type=='CE' else 'pe'}_strike_mode", "atm")

    if mode == "itm":
        if option_type == "CE":
            base = math.floor(spot / interval) * interval
            if abs(spot - base) < 0.01:
                base -= interval
            return base
        else:
            base = math.ceil(spot / interval) * interval
            if abs(spot - base) < 0.01:
                base += interval
            return base
    # ATM
    return round(spot / interval) * interval


def bs_estimate(instrument: str, spot: float,
                strike: float, option_type: str, days: int) -> float:
    """
    Black-Scholes ATM approximation used as LTP fallback when Zerodha returns 0.
    Shows as 'estimated' in the UI. Constraint 9 — not the primary display value.

    Formula: premium = intrinsic + S×σ×√(T/252)×0.3989
    """
    sigma     = config.OPTION_VOLATILITY.get(instrument, 0.15)
    T         = max(days, 1) / 252
    atm_time  = spot * sigma * math.sqrt(T) * 0.3989
    intrinsic = max(spot - strike, 0) if option_type == "CE" else max(strike - spot, 0)
    return round(max(intrinsic + atm_time, 0.5), 2)


# ── Zerodha Feed ───────────────────────────────────────────────────────────────

class ZerodhaFeed:
    # Network errors that are TRANSIENT — always retry, never mark feed as dead
    _TRANSIENT = (
        "ConnectionResetError", "ConnectionError", "RemoteDisconnected",
        "ChunkedEncodingError", "ReadTimeout", "ConnectTimeout",
        "ProtocolError", "Connection aborted", "Connection reset",
        "Read timed out", "Network is unreachable",
    )
    # Maximum retries for a single REST call on transient errors
    _MAX_RETRIES   = 3
    # Base backoff in seconds between retries (doubles each attempt)
    _RETRY_BASE    = 0.25

    def __init__(self, api_key: str = "", access_token: str = ""):
        self._api_key  = api_key
        self._token    = access_token
        self._kite     = None
        self._ok       = False          # auth/connection status
        self._net_ok   = True           # network health (transient errors don't flip _ok)
        self._net_fail = 0              # consecutive network failure count
        if api_key and access_token:
            self._connect()

    def _connect(self) -> None:
        try:
            from kiteconnect import KiteConnect
            self._kite = KiteConnect(api_key=self._api_key)
            self._kite.set_access_token(self._token)
            p = self._kite.profile()
            self._ok = True
            logger.info(
                f"Zerodha ✓  user={p.get('user_name','?')}  "
                f"email={p.get('email','?')}"
            )
        except ImportError:
            logger.error("kiteconnect not installed — run: pip install kiteconnect")
        ###ZERODHA connection issue fix
        # except Exception as exc:
        #     self._ok = False
        #     if "TokenException" in str(exc) or "Invalid" in str(exc):
        #         logger.error("Zerodha: access token expired — regenerate via login")
        #     else:
        #         logger.error(f"Zerodha connect failed: {exc}")
        ##########################################
        ###ZERODHA connection issue fix
        except Exception as exc:
            self._ok = False
            msg      = str(exc)
            exc_type = type(exc).__name__
            # Zerodha raises TokenException but str(exc) contains the message body,
            # NOT the class name. The actual message is:
            #   "Incorrect `api_key` or `access_token`."
            # Check both the exception type and key phrases in the message.
            is_auth_error = (
                exc_type in ("TokenException", "InputException") or
                any(k in msg for k in
                    ("api_key", "access_token", "Incorrect", "TokenException"))
            )
            if is_auth_error:
                logger.error(
                    "Zerodha: access_token is invalid or expired.\n"
                    "  IMPORTANT — The access_token must be regenerated EVERY MORNING.\n"
                    "  It expires at midnight IST regardless of how recently it was used.\n"
                    "  The API key itself is permanent — the token is the issue.\n"
                    "  ACTION: Click 'Login with Zerodha' on the dashboard to get today's token.\n"
                    f"  Zerodha error detail: {msg}"
                )
            else:
                logger.error(f"Zerodha connect failed: {msg}")
            ######################################

    def _is_transient(self, exc: Exception) -> bool:
        """
        Returns True if the exception is a transient network error that should
        be retried without marking the feed as permanently disconnected.
        Auth errors (TokenException, InputException) are NOT transient.
        """
        msg      = str(exc)
        exc_type = type(exc).__name__
        # Explicit auth errors → not transient
        if exc_type in ("TokenException", "InputException"):
            return False
        if any(k in msg for k in ("api_key", "access_token", "Incorrect", "TokenException")):
            return False
        # Network / transport errors → transient
        return any(k in msg or k in exc_type for k in self._TRANSIENT)

    def _retry(self, fn, *args, label: str = "REST call", **kwargs):
        """
        Execute fn(*args, **kwargs) with up to _MAX_RETRIES retries on transient
        network errors. Uses exponential backoff. Returns the result or raises
        the last exception if all retries are exhausted.

        On success after retries: resets _net_fail counter, logs recovery.
        On persistent transient failure: increments _net_fail, logs degraded state.
        Auth errors bypass retry and propagate immediately.
        """
        import time as _time
        last_exc = None
        for attempt in range(1, self._MAX_RETRIES + 1):
            try:
                result = fn(*args, **kwargs)
                if self._net_fail > 0:
                    logger.info(
                        f"Network recovered after {self._net_fail} failure(s) — {label}"
                    )
                    self._net_fail = 0
                    self._net_ok   = True
                return result
            except Exception as exc:
                last_exc = exc
                if not self._is_transient(exc):
                    raise   # auth errors propagate immediately
                wait = self._RETRY_BASE * (2 ** (attempt - 1))   # 0.25 → 0.5 → 1.0s
                if attempt < self._MAX_RETRIES:
                    logger.debug(
                        f"Transient error on {label} (attempt {attempt}/{self._MAX_RETRIES}): "
                        f"{exc}  retrying in {wait:.2f}s …"
                    )
                    _time.sleep(wait)
                else:
                    self._net_fail += 1
                    self._net_ok    = False
                    # Log at WARNING every 5th consecutive failure to avoid log flood
                    if self._net_fail == 1 or self._net_fail % 5 == 0:
                        logger.warning(
                            f"Network error ({self._net_fail} consecutive): {label}  "
                            f"detail={exc}  "
                            f"System continues with last-known values — will auto-recover."
                        )
                    else:
                        logger.debug(f"Network error #{self._net_fail}: {label}: {exc}")
        return None   # caller handles None as graceful degradation

    # ── Auth helpers ──────────────────────────────────────────────────────────
        return f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"

    def generate_session(self, request_token: str, api_secret: str) -> str:
        try:
            if not self._kite:
                from kiteconnect import KiteConnect
                self._kite = KiteConnect(api_key=self._api_key)
            data  = self._kite.generate_session(request_token, api_secret=api_secret)
            self._token = data["access_token"]
            self._kite.set_access_token(self._token)
            self._ok = True
            logger.info("Zerodha OAuth session generated ✓")
            return self._token
        except Exception as exc:
            logger.error(f"Zerodha session generation: {exc}")
            return ""

    def set_token(self, token: str) -> bool:
        self._token = token
        self._connect()
        return self._ok

    def is_connected(self) -> bool:
        return self._ok

    # ── Market data ───────────────────────────────────────────────────────────

    def get_quote(self, instrument: str) -> dict:
        """
        Fetch index LTP, OHLC with automatic retry on transient network errors.
        Only sets self._ok = False on confirmed auth (token) failures.
        Transient errors return {} but leave the feed alive for the next tick.
        """
        if not self._ok:
            return {}
        sym = config.INSTRUMENTS[instrument]["zerodha_symbol"]
        try:
            raw = self._retry(self._kite.quote, [sym], label=f"quote {instrument}")
            if raw is None:
                return {}   # all retries failed — return empty, system keeps running
            q    = raw.get(sym, {})
            ltp  = float(q.get("last_price", 0) or 0)
            if ltp == 0:
                return {}
            ohlc = q.get("ohlc", {})
            prev = float(ohlc.get("close", 0) or 0)
            chg  = round(ltp - prev, 2)
            return {
                "last_price": ltp,
                "open":       float(ohlc.get("open", 0) or 0),
                "high":       float(ohlc.get("high", 0) or 0),
                "low":        float(ohlc.get("low",  0) or 0),
                "prev_close": prev,
                "change":     chg,
                "pchange":    round(chg / prev * 100, 2) if prev else 0.0,
            }
        except Exception as exc:
            # Only confirmed token failures permanently disconnect the feed
            if "TokenException" in str(exc) or type(exc).__name__ == "TokenException":
                self._ok = False
                logger.error("Zerodha token expired — reconnect via dashboard")
            else:
                # Transient error not caught by _retry (should not happen, but safety net)
                logger.warning(f"get_quote({instrument}): {exc} — using last-known values")
            return {}

    def get_option_ltp(self, symbol: str, instrument: str = "") -> float:
        """
        Fetch REAL option last traded price with automatic retry on network errors.
        Returns 0.0 on failure — caller falls back to last-known premium or BS estimate.
        Never sets self._ok = False (transient errors do not disconnect the feed).
        """
        if not self._ok:
            return 0.0
        cfg_inst = config.INSTRUMENTS.get(instrument, {})
        opt_exch = cfg_inst.get("options_exchange", "NFO")
        full     = f"{opt_exch}:{symbol}"

        # First attempt: ltp() (faster)
        raw = self._retry(self._kite.ltp, [full], label=f"option_ltp {symbol}")
        if raw is not None:
            ltp = float(raw.get(full, {}).get("last_price", 0) or 0)
            if ltp > 0:
                return ltp

        # Fallback: quote() (sometimes ltp() misses non-traded options)
        raw2 = self._retry(self._kite.quote, [full], label=f"option_quote {symbol}")
        if raw2 is not None:
            ltp = float(raw2.get(full, {}).get("last_price", 0) or 0)
            if ltp > 0:
            return ltp

            return 0.0

    def place_order(self, symbol: str, qty: int,
                    transaction_type: str, instrument: str = "") -> str | None:
        if not self._ok:
            return None
        cfg_inst = config.INSTRUMENTS.get(instrument, {})
        opt_exch = cfg_inst.get("options_exchange", "NFO")
        try:
            from kiteconnect import KiteConnect
            oid = self._kite.place_order(
                tradingsymbol    = symbol,
                exchange         = opt_exch,
                transaction_type = transaction_type,
                quantity         = qty,
                order_type       = self._kite.ORDER_TYPE_MARKET,
                product          = self._kite.PRODUCT_MIS,
                variety          = self._kite.VARIETY_REGULAR,
            )
            logger.info(f"Live order placed: {opt_exch}:{symbol} qty={qty} → id={oid}")
            return str(oid)
        except Exception as exc:
            logger.error(f"Order failed {symbol}: {exc}")
            return None

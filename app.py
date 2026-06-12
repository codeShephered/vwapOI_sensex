"""
app.py — NSE Options Trading System V2 (Flask).

Entry pipeline (per candle close):
  L1 : WebSocket tick → price feed (KiteTicker ~100ms)
  L2 : CandleBuilder → 5-min OHLCV
  L3 : VWAP + Regime update
  L4 : OI Analysis (every 5-min candle, uses Zerodha quote)
  L5 : Pattern scan (last 3 candles)
  L6 : Trade Scoring (Pattern + VWAP + OI + Volume)
  L7 : Entry gate (score >= MIN_TRADE_SCORE, time window, no open position)
  L8 : Place paper/live trade

Exit pipeline (tick + candle):
  Tick  : Premium SL (20% initial, breakeven at +25%, trailing at +40%)
           PSAR exit (trending markets only)
  Candle: SAR reversal | Tier1 reversal pattern
  Time  : 3:15 PM force-close

PSAR is used ONLY for exit — never for entry confirmation.
"""
from __future__ import annotations
import json, os, queue, threading
from datetime import datetime

from flask import (Flask, render_template, Response, jsonify,
                   request, redirect, url_for, send_file)

import config
from logger_setup  import setup_logger, get_module_logger, get_log_entries, LOG_QUEUE
from data_feed     import ZerodhaFeed, bs_estimate, get_nifty_expiry, get_banknifty_expiry, get_sensex_expiry, get_expiry
from candle_builder import CandleBuilder
from pattern_engine import PatternEngine
from parabolic_sar  import ParabolicSAR
from vwap_engine    import VWAPEngine, VWAPState
from oi_engine      import OIEngine, OIState, OI_NEUTRAL
from score_engine   import compute_score, ScoreResult
from trade_engine   import TradeEngine

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

_root  = setup_logger("trading")
logger = get_module_logger("App")

# ── NSE WebSocket tokens ──────────────────────────────────────────────────────
_NSE_TOKENS = {
    256265: "NIFTY",
    260105: "BANKNIFTY",
    265:    "SENSEX",      # BSE:SENSEX
}
_TOKEN_BY_INST = {v: k for k, v in _NSE_TOKENS.items()}

from datetime import time as _dtime
_MARKET_OPEN  = _dtime(9, 15)
_MARKET_CLOSE = _dtime(15, 30)

# ── Shared state ──────────────────────────────────────────────────────────────
state: dict = {
    "mode":        config.TRADING_MODE,
    "running":     False,
    "connected":   False,
    "last_tick":   None,
    "market":      {inst: {} for inst in config.INSTRUMENTS},
    "signals":     {inst: {} for inst in config.INSTRUMENTS},
    "sar":         {inst: {} for inst in config.INSTRUMENTS},
    "vwap":        {inst: {} for inst in config.INSTRUMENTS},
    "oi":          {inst: {} for inst in config.INSTRUMENTS},
    "score":       {inst: {} for inst in config.INSTRUMENTS},
    "candle":      {inst: {} for inst in config.INSTRUMENTS},
    "regime":      {inst: "UNKNOWN" for inst in config.INSTRUMENTS},
}

zerodha_feed: ZerodhaFeed | None = None
_ticker       = None
_ticker_lock  = threading.Lock()

candle_builders = {inst: CandleBuilder(inst) for inst in config.INSTRUMENTS}
sar_trackers    = {inst: ParabolicSAR()       for inst in config.INSTRUMENTS}
vwap_engines    = {inst: VWAPEngine(inst)     for inst in config.INSTRUMENTS}
oi_engines      = {inst: OIEngine(inst)       for inst in config.INSTRUMENTS}
sar_prev_bull:   dict[str, bool | None] = {inst: None for inst in config.INSTRUMENTS}

pattern_engine = PatternEngine()
trade_engine   = TradeEngine()

_sse_clients: list[queue.Queue] = []
_sse_lock     = threading.Lock()
_stop_evt     = threading.Event()

# Track last session date for VWAP reset
_last_session_date: dict[str, str] = {inst: "" for inst in config.INSTRUMENTS}

# Running average volume per candle
_vol_history: dict[str, list[int]] = {inst: [] for inst in config.INSTRUMENTS}


# ── SSE ───────────────────────────────────────────────────────────────────────

def _broadcast(data: str) -> None:
    with _sse_lock:
        dead = [q for q in _sse_clients if not _try_put(q, data)]
        for q in dead:
            _sse_clients.remove(q)

def _try_put(q: queue.Queue, data: str) -> bool:
    try:
        q.put_nowait(data); return True
    except queue.Full:
        return False

def _log_loop() -> None:
    while True:
        try:
            _broadcast(LOG_QUEUE.get(timeout=1))
        except queue.Empty:
            pass


# ── Zerodha helpers ───────────────────────────────────────────────────────────

def _feed_ok() -> bool:
    return zerodha_feed is not None and zerodha_feed.is_connected()

def _real_ltp(pos, spot: float, instrument: str) -> float:
    """
    Get option LTP for an open position.
    Priority: real Zerodha LTP → last-known premium (on network error) → BS estimate.
    Using last-known premium on transient errors prevents stale BS values from
    triggering the premium SL incorrectly during brief network hiccups.
    """
    if _feed_ok():
        ltp = zerodha_feed.get_option_ltp(pos.zerodha_symbol, instrument)
        if ltp > 0:
            return ltp
    # On network error: last-known premium is more reliable than a new BS estimate
    # because BS estimate changes with spot movement but may not reflect real IV
    if pos.current_premium > 0:
        return pos.current_premium
    # Only fall back to BS if no premium has ever been recorded
    return bs_estimate(
        instrument, spot, pos.strike, pos.option_type,
        max((pos.expiry - __import__("datetime").date.today()).days, 1),
    )


# ── Core pipeline ─────────────────────────────────────────────────────────────

def _process(instrument: str, price: float) -> None:
    """
    Process one price tick for one instrument.
    Called from KiteTicker on_ticks on every WebSocket tick.
    """
    state["market"][instrument] = {"last_price": price}

    cb  = candle_builders[instrument]
    sar = sar_trackers[instrument]
    ve  = vwap_engines[instrument]
    oe  = oi_engines[instrument]

    # ── L2: candle builder ─────────────────────────────────────────────────
    completed = cb.update(price)
    cur = cb.current()
    if cur:
        state["candle"][instrument] = cur.to_dict()

    # Session date change → reset VWAP
    today_str = datetime.now().strftime("%Y-%m-%d")
    if today_str != _last_session_date[instrument]:
        ve.new_session()
        _last_session_date[instrument] = today_str
        _vol_history[instrument].clear()
        logger.info(f"{instrument}: new session, VWAP reset")

    # ── Exit check on every tick ───────────────────────────────────────────
    if trade_engine.has_open(instrument):
        pos = trade_engine.get_pos(instrument)
        if pos:
            ltp    = _real_ltp(pos, price, instrument)
            sv     = sar.value
            regime = state["regime"].get(instrument, "UNKNOWN")
            result = trade_engine.check_tick(instrument, price, ltp, sv, regime)
            if result:
                state["signals"][instrument] = {
                    "type": "exit", "reason": result["exit_reason"],
                    "ts":   result["exit_time"],
                }
                return

    # ── Only on candle close ───────────────────────────────────────────────
    if completed is None:
        return

    # ── L3: VWAP + regime ─────────────────────────────────────────────────
    vwap_state: VWAPState = ve.update(completed)
    state["vwap"][instrument]   = {
        "value":       vwap_state.vwap,
        "slope":       vwap_state.slope,
        "dist_pct":    vwap_state.distance_pct,
        "near_vwap":   vwap_state.near_vwap,
        "price_above": vwap_state.price_above,
        "ema_fast":    vwap_state.ema_fast,
        "ema_slow":    vwap_state.ema_slow,
    }
    state["regime"][instrument] = vwap_state.regime

    # ── L3b: Parabolic SAR (exit management only) ──────────────────────────
    sar_val, sar_bull = sar.update(completed)
    if sar_val is not None:
        state["sar"][instrument] = sar.to_dict()
        prev         = sar_prev_bull[instrument]
        sar_reversed = prev is not None and prev != sar_bull
        sar_prev_bull[instrument] = sar_bull
    else:
        sar_val, sar_bull, sar_reversed = 0.0, True, False

    last3 = cb.get_last_n(3)

    # ── L4: OI analysis (candle-close, every 5 min) ───────────────────────
    kite_obj   = zerodha_feed._kite if _feed_ok() else None
    oi_state: OIState = oe.analyse(price, kite_obj)
    state["oi"][instrument] = {
        "signal":          oi_state.signal,
        "call_oi_change":  oi_state.call_oi_change,
        "put_oi_change":   oi_state.put_oi_change,
        "pcr":             oi_state.pcr,
        "support_strike":  oi_state.support_strike,
        "resist_strike":   oi_state.resist_strike,
    }

    # ── Candle-close exit ──────────────────────────────────────────────────
    if trade_engine.has_open(instrument):
        pos = trade_engine.get_pos(instrument)
        if pos:
            rev, _ = pattern_engine.is_reversal_of(last3, pos.direction)
            ltp    = _real_ltp(pos, price, instrument)
            result = trade_engine.check_candle(instrument, ltp, sar_val, sar_reversed, rev or "")
            if result:
                state["signals"][instrument] = {
                    "type": "exit", "reason": result["exit_reason"],
                    "ts":   result["exit_time"],
                }
        return   # no new entry while position open

    # ── L5: Pattern scan ──────────────────────────────────────────────────
    if not trade_engine.can_enter():
        return
    if not sar.is_ready or not cb.has_min(config.SAR_SEED_CANDLES):
        return

    pattern, direction = pattern_engine.scan(last3)
    if not pattern:
        state["signals"][instrument] = {"type": "none"}
        return

    # ── L6: Trade scoring ─────────────────────────────────────────────────
    vol_hist = _vol_history[instrument]
    vol_hist.append(completed.volume)
    if len(vol_hist) > 20:
        vol_hist.pop(0)
    avg_vol  = sum(vol_hist) / len(vol_hist) if vol_hist else 0

    score: ScoreResult = compute_score(
        pattern, direction, vwap_state, oi_state,
        candle_volume=completed.volume, avg_volume=avg_vol,
    )
    state["score"][instrument] = {
        "total":         score.total,
        "passes":        score.passes,
        "breakdown":     score.breakdown,
        "tier":          score.tier,
    }
    logger.info(f"{instrument}: {pattern} {direction.upper()}  {score.breakdown}")

    if not score.passes:
        state["signals"][instrument] = {"type": "none"}
        return

    # ── L7: Entry gate ────────────────────────────────────────────────────
    # NOTE: PSAR direction is NOT used as entry gate — per spec.
    # Entry is based on VWAP alignment + OI + pattern + score only.

    # VWAP direction must agree with trade direction
    if direction == "bullish" and not vwap_state.price_above:
        logger.debug(f"{instrument}: {pattern} CE blocked — price below VWAP")
        state["signals"][instrument] = {"type": "none"}
        return
    if direction == "bearish" and vwap_state.price_above:
        logger.debug(f"{instrument}: {pattern} PE blocked — price above VWAP")
        state["signals"][instrument] = {"type": "none"}
        return

    # ── L8: Place trade ───────────────────────────────────────────────────
    signal   = trade_engine.build_signal(instrument, direction, price, pattern, score.total)
    real_ltp = zerodha_feed.get_option_ltp(signal["zerodha_symbol"]) if _feed_ok() else 0.0

    if state["mode"] == "live" and _feed_ok():
        from kiteconnect import KiteConnect
        zerodha_feed.place_order(
            signal["zerodha_symbol"], signal["lot_size"],
            KiteConnect.TRANSACTION_TYPE_BUY, instrument
        )

    pos = trade_engine.enter(signal, real_ltp)
    state["signals"][instrument] = {
        "type":      "entry",
        "direction": direction,
        "pattern":   pattern,
        "tier":      score.tier,
        "score":     score.total,
        "breakdown": score.breakdown,
        "opt_type":  signal["option_type"],
        "strike":    signal["strike"],
        "expiry":    signal["expiry_str"],
        "regime":    vwap_state.regime,
        "premium":   pos.entry_premium,
        "estimated": pos.is_estimated,
        "ts":        datetime.now().strftime("%H:%M:%S"),
    }


# ── KiteTicker WebSocket ──────────────────────────────────────────────────────

def _start_ticker(api_key: str, access_token: str) -> None:
    global _ticker
    with _ticker_lock:
        if _ticker is not None:
            try: _ticker.stop()
            except Exception: pass
            _ticker = None

    from kiteconnect import KiteTicker
    ticker = KiteTicker(api_key, access_token, reconnect=True,
                        reconnect_max_tries=300, reconnect_max_delay=60,
                        connect_timeout=60)
    tokens = list(_NSE_TOKENS.keys())

    def on_ticks(ws, ticks):
        if not state["running"]: return
        now = datetime.now().time()
        if now < _MARKET_OPEN or now > _MARKET_CLOSE: return
        state["last_tick"]  = datetime.now().strftime("%H:%M:%S")
        state["connected"]  = True
        for tick in ticks:
            inst  = _NSE_TOKENS.get(tick.get("instrument_token"))
            if not inst: continue
            price = float(tick.get("last_price", 0) or 0)
            if price <= 0: continue
            try:
                _process(inst, price)
            except Exception as exc:
                logger.error(f"Tick error {inst}: {exc}", exc_info=True)
        if trade_engine.must_square_off():
            for c in trade_engine.force_close_all():
                logger.info(f"Square-off {c['instrument']} {c['option_type']} PnL=₹{c['final_pnl']:.2f}")

    def on_connect(ws, response):
        logger.info("KiteTicker connected ✓  Subscribed: NIFTY + BANKNIFTY + SENSEX")
        ws.subscribe(tokens)
        ws.set_mode(ws.MODE_QUOTE, tokens)
        state["connected"] = True

    def on_reconnect(ws, n):
        logger.warning(f"KiteTicker reconnecting … attempt {n}")
        state["connected"] = False

    def on_noreconnect(ws):
        logger.error("KiteTicker: all retries exhausted — attempting restart")
        state["connected"] = False
        if state["running"] and config.ZERODHA_ACCESS_TOKEN:
            import threading as _t
            _t.Timer(5.0, _start_ticker,
                     args=(config.ZERODHA_API_KEY, config.ZERODHA_ACCESS_TOKEN)).start()

    def on_error(ws, code, reason):
        logger.error(f"KiteTicker error {code}: {reason}")

    def on_close(ws, code, reason):
        logger.warning(f"KiteTicker closed {code}: {reason}")
        state["connected"] = False
        if state["running"] and code == 1006 and config.ZERODHA_ACCESS_TOKEN:
            import threading as _t
            _t.Timer(3.0, _start_ticker,
                     args=(config.ZERODHA_API_KEY, config.ZERODHA_ACCESS_TOKEN)).start()

    ticker.on_ticks        = on_ticks
    ticker.on_connect      = on_connect
    ticker.on_reconnect    = on_reconnect
    ticker.on_noreconnect  = on_noreconnect
    ticker.on_error        = on_error
    ticker.on_close        = on_close

    with _ticker_lock:
        _ticker = ticker
    ticker.connect(threaded=True)
    logger.info(f"KiteTicker started — tokens: {tokens}")


def _stop_ticker() -> None:
    global _ticker
    with _ticker_lock:
        if _ticker:
            try: _ticker.stop()
            except Exception: pass
            _ticker = None
    state["connected"] = False


def _watchdog() -> None:
    import time as _t
    last = _t.monotonic()
    while not _stop_evt.is_set():
        _stop_evt.wait(60)
        elapsed = _t.monotonic() - last
        last    = _t.monotonic()
        if elapsed > 90 and state["running"] and config.ZERODHA_ACCESS_TOKEN:
            logger.info(f"Woke from sleep (~{int(elapsed-60)}s) — restarting ticker")
            _start_ticker(config.ZERODHA_API_KEY, config.ZERODHA_ACCESS_TOKEN)


# ── Flask routes ──────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html",
                           mode=state["mode"],
                           running=state["running"],
                           connected=state["connected"],
                           expiry_info={inst: get_expiry(inst).strftime("%d-%b-%Y")
                                        for inst in config.INSTRUMENTS})

@app.route("/api/status")
def api_status():
    return jsonify({
        "mode":       state["mode"],
        "running":    state["running"],
        "connected":  state["connected"],
        "last_tick":  state["last_tick"],
        "market":     state["market"],
        "signals":    state["signals"],
        "sar":        state["sar"],
        "vwap":       state["vwap"],
        "oi":         state["oi"],
        "score":      state["score"],
        "candle":     state["candle"],
        "regime":     state["regime"],
        "positions":  trade_engine.all_positions(),
        "history":    trade_engine.history()[-20:],
        "stats":      trade_engine.get_stats(),
        "expiry": {inst: get_expiry(inst).strftime("%d-%b-%Y")
                   for inst in config.INSTRUMENTS},
        "network_ok":    zerodha_feed._net_ok   if zerodha_feed else True,
        "net_fail_count": zerodha_feed._net_fail if zerodha_feed else 0,
    })

@app.route("/api/start", methods=["POST"])
def api_start():
    if state["running"]:
        return jsonify({"status": "already_running"})
    if not _feed_ok():
        return jsonify({"error": "Zerodha not connected — login first"}), 400
    state["running"] = True
    _start_ticker(config.ZERODHA_API_KEY, config.ZERODHA_ACCESS_TOKEN)
    logger.info(f"System STARTED  mode={state['mode'].upper()}")
    return jsonify({"status": "started", "mode": state["mode"]})

@app.route("/api/stop", methods=["POST"])
def api_stop():
    state["running"] = False
    _stop_ticker()
    logger.info("System STOPPED")
    return jsonify({"status": "stopped"})

@app.route("/api/emergency-exit", methods=["POST"])
def api_emergency_exit():
    closed = trade_engine.force_close_all("Emergency exit (user)")
    logger.info(f"EMERGENCY EXIT: {len(closed)} position(s) closed")
    return jsonify({"status": "closed", "count": len(closed), "records": closed})

@app.route("/api/exit-trade", methods=["POST"])
def api_exit_trade():
    data       = request.get_json(silent=True) or {}
    instrument = (data.get("instrument") or "").strip().upper()
    if instrument not in config.INSTRUMENTS:
        return jsonify({"error": f"Unknown instrument '{instrument}'"}), 400
    if not trade_engine.has_open(instrument):
        return jsonify({"error": f"No open position for {instrument}"}), 404
    record = trade_engine.close_position(instrument, "Manual exit (user)")
    if not record:
        return jsonify({"error": "Close failed"}), 409
    state["signals"][instrument] = {
        "type": "exit", "reason": record.get("exit_reason", "Manual"),
        "ts": record.get("exit_time", ""),
    }
    logger.info(f"Manual exit {instrument}  PnL=₹{record.get('final_pnl',0):+.2f}")
    return jsonify({"status": "closed", "record": record})

@app.route("/api/mode", methods=["POST"])
def api_mode():
    if state["running"]:
        return jsonify({"error": "Stop system before switching mode"}), 400
    data = request.get_json(silent=True) or {}
    m    = data.get("mode", "paper")
    if m not in ("paper", "live"):
        return jsonify({"error": "Invalid mode"}), 400
    state["mode"]        = m
    config.TRADING_MODE  = m
    logger.info(f"Mode → {m.upper()}")
    return jsonify({"status": "ok", "mode": m})

@app.route("/api/save-key", methods=["POST"])
def api_save_key():
    data = request.get_json(silent=True) or {}
    key  = data.get("api_key", "").strip()
    if not key:
        return jsonify({"error": "Empty API key"}), 400
    config.ZERODHA_API_KEY = key
    logger.info(f"API key saved: {key[:6]}…")
    return jsonify({"status": "ok"})

@app.route("/zerodha/login")
def zerodha_login():
    if not config.ZERODHA_API_KEY:
        return "Set ZERODHA_API_KEY in config.py first", 400
    return redirect(ZerodhaFeed.login_url(config.ZERODHA_API_KEY))

@app.route("/zerodha/callback")
@app.route("/callback")
def zerodha_callback():
    global zerodha_feed
    req_token = request.args.get("request_token", "")
    if not req_token:
        return "Missing request_token", 400
    if zerodha_feed is None:
        zerodha_feed = ZerodhaFeed(config.ZERODHA_API_KEY)
    token = zerodha_feed.generate_session(req_token, config.ZERODHA_API_SECRET)
    if token:
        config.ZERODHA_ACCESS_TOKEN = token
        state["connected"] = True
        logger.info("Zerodha OAuth login successful ✓")
        return redirect(url_for("index"))
    return ("<h2>Authentication failed</h2>"
            "<p>Check ZERODHA_API_SECRET in config.py</p>"
            "<p><a href='/'>← Back</a></p>")

@app.route("/api/set-token", methods=["POST"])
def api_set_token():
    global zerodha_feed
    data  = request.get_json(silent=True) or {}
    token = data.get("access_token", "").strip()
    key   = data.get("api_key", config.ZERODHA_API_KEY).strip()
    if not token or not key:
        return jsonify({"error": "Missing token or key"}), 400
    config.ZERODHA_API_KEY      = key
    config.ZERODHA_ACCESS_TOKEN = token
    if zerodha_feed is None:
        zerodha_feed = ZerodhaFeed(key, token)
    else:
        zerodha_feed._api_key = key
        zerodha_feed.set_token(token)
    state["connected"] = zerodha_feed.is_connected()
    if state["connected"]:
        logger.info("Manual token accepted ✓")
        return jsonify({"status": "connected"})
    return jsonify({"status": "failed",
                    "message": "Token rejected — regenerate from kite.zerodha.com"}), 400

@app.route("/stream")
def stream():
    def gen():
        cq = queue.Queue(maxsize=1000)
        with _sse_lock:
            _sse_clients.append(cq)
        for e in get_log_entries()[-100:]:
            yield f"data: {json.dumps(e)}\n\n"
        try:
            while True:
                try:
                    yield f"data: {cq.get(timeout=25)}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        except GeneratorExit:
            with _sse_lock:
                try: _sse_clients.remove(cq)
                except ValueError: pass
    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.route("/api/logs")
def api_logs():
    if not os.path.exists(config.LOG_FILE):
        return jsonify({"error": "Log file not found"}), 404
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(config.LOG_FILE, as_attachment=True,
                     download_name=f"trading_{ts}.log")

@app.route("/health")
def health():
    return jsonify({"running": state["running"], "connected": state["connected"],
                    "last_tick": state["last_tick"]})


# ── Startup ───────────────────────────────────────────────────────────────────

def _init() -> None:
    global zerodha_feed
    logger.info("=" * 62)
    logger.info("  NSE Options Trading System V2")
    logger.info(f"  Entry: VWAP + OI + Candlestick + Score >= {config.MIN_TRADE_SCORE}")
    logger.info(f"  PSAR : EXIT management only (not for entry)")
    logger.info(f"  SL   : {int(config.PREMIUM_SL_PCT*100)}%  BE: +{int(config.BREAKEVEN_TRIGGER_PCT*100)}%  Trail: +{int(config.TRAIL_TRIGGER_PCT*100)}%")
    logger.info(f"  Window: {config.ENTRY_START_HOUR}:{config.ENTRY_START_MINUTE:02d} → {config.NO_NEW_TRADE_HOUR}:{config.NO_NEW_TRADE_MINUTE:02d}  Square-off: {config.SQUARE_OFF_HOUR}:{config.SQUARE_OFF_MINUTE:02d}")
    for inst in config.INSTRUMENTS:
        logger.info(f"  {inst:<12} expiry: {get_expiry(inst).strftime('%d-%b-%Y')}")
    logger.info("=" * 62)

    if config.ZERODHA_API_KEY and len(config.ZERODHA_ACCESS_TOKEN) >= 10:
        zerodha_feed = ZerodhaFeed(config.ZERODHA_API_KEY, config.ZERODHA_ACCESS_TOKEN)
        state["connected"] = zerodha_feed.is_connected()
        if not state["connected"]:
            logger.warning(
                "Zerodha auto-connect failed.\n"
                "  Access token expires at midnight IST — regenerate via dashboard login."
            )
    else:
        logger.info("No Zerodha credentials — login via dashboard")

    threading.Thread(target=_log_loop, daemon=True,  name="LogBroadcast").start()
    threading.Thread(target=_watchdog, daemon=False, name="Watchdog").start()
    logger.info(f"Dashboard → http://localhost:{config.FLASK_PORT}")


_init()

if __name__ == "__main__":
    try:
        from waitress import serve
        logger.info("Waitress WSGI server starting")
        serve(app, host=config.FLASK_HOST, port=config.FLASK_PORT, threads=8)
    except ImportError:
        logger.warning("waitress not installed — using Flask dev server")
        app.run(host=config.FLASK_HOST, port=config.FLASK_PORT,
                debug=False, threaded=True, use_reloader=False)

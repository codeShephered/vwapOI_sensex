# NSE Options Trading System V2

A professional intraday options trading system for NIFTY and BANKNIFTY built on Zerodha KiteConnect.

## Strategy Overview

| Component | V2 Behaviour |
|---|---|
| **Entry** | VWAP alignment + Option Chain OI + Candlestick Pattern + Trade Score |
| **PSAR** | Exit management only — **never used for entry** |
| **Pattern tiers** | Tier 1 (100 pts): Three White Soldiers, Morning Doji Star, Three Black Crows, Evening Doji Star · Tier 2 (70 pts): Bullish Engulfing, Morning Star, Bearish Engulfing, Evening Star |
| **Trade score gate** | Pattern + VWAP (20) + OI (20) + Near-VWAP (20) + Volume (10). Minimum **90 points** to trade |
| **Initial SL** | 20% premium drop from entry |
| **Breakeven** | At +25% profit → SL moves to entry premium |
| **Trailing** | At +40% profit → trail 15% from highest premium |
| **PSAR exit** | Triggered in TRENDING_UP / TRENDING_DOWN regime only |
| **Trading window** | 9:30 AM – 2:45 PM · Square-off: 3:15 PM |

---

## System Architecture

```
trading_system_v2/
├── app.py               ← Main Flask app + pipeline
├── config.py            ← All parameters
├── candle_builder.py    ← 5-min OHLCV builder
├── parabolic_sar.py     ← SAR (TradingView-aligned, exit only)
├── pattern_engine.py    ← 8 candlestick patterns with tier scoring
├── vwap_engine.py       ← VWAP + slope + market regime
├── oi_engine.py         ← Option chain OI analysis
├── score_engine.py      ← Trade scoring (VWAP + OI + pattern + volume)
├── trade_engine.py      ← Position lifecycle + risk management
├── data_feed.py         ← Zerodha KiteConnect data utilities
├── logger_setup.py      ← Rotating log + SSE broadcast
├── install_service.py   ← OS service installer
├── requirements.txt
├── templates/
│   └── index.html       ← Dashboard
└── README.md
```

---

## Prerequisites

| Item | Requirement |
|---|---|
| Python | 3.11 or 3.12 |
| Zerodha account | Active trading account |
| Zerodha API | Subscription from developers.kite.trade (₹2,000/month) |
| Access token | Regenerated every morning — expires midnight IST |

---

## Installation

### macOS

```bash
# 1. Install Python 3.12 if needed
brew install python@3.12

# 2. Create virtual environment
cd /path/to/trading_system_v2
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Verify
python3 -c "import kiteconnect, flask; print('OK')"
```

### Linux (Ubuntu / Debian)

```bash
# 1. Install Python 3.12
sudo apt update && sudo apt install python3.12 python3.12-venv -y

# 2. Create virtual environment
cd /path/to/trading_system_v2
python3.12 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
```

### Windows

```powershell
# 1. Install Python 3.12 from https://www.python.org/downloads/
# During install: check "Add Python to PATH"

# 2. Open PowerShell and navigate to project folder
cd C:\path\to\trading_system_v2

# 3. Create virtual environment
python -m venv venv
venv\Scripts\activate

# 4. Install dependencies
pip install -r requirements.txt
```

---

## Configuration

Open `config.py` and set:

```python
ZERODHA_API_KEY    = "your_permanent_api_key"
ZERODHA_API_SECRET = "your_api_secret"
# Leave ZERODHA_ACCESS_TOKEN blank — set via dashboard each morning
```

### Tunable parameters

```python
MIN_TRADE_SCORE      = 90     # minimum score to take a trade (raise to be selective)
PREMIUM_SL_PCT       = 0.20   # initial SL: 20% drop (change to 0.15 for tighter SL)
BREAKEVEN_TRIGGER_PCT= 0.25   # move SL to breakeven at +25% profit
TRAIL_TRIGGER_PCT    = 0.40   # activate trailing at +40% profit
TRAIL_RETRACE_PCT    = 0.15   # exit when premium retraces 15% from highest
NO_NEW_TRADE_HOUR    = 14     # no new entries after 2:45 PM
NO_NEW_TRADE_MINUTE  = 45
```

---

## Daily Startup (every morning)

### Step 1 — Get today's access token

The access token expires at midnight IST every day. Regenerate it each morning before starting.

**Option A — Via dashboard (recommended)**
1. Start the system: `python app.py`
2. Open `http://localhost:5001`
3. Enter your API Key and today's Access Token in the sidebar
4. Click **Connect Zerodha**

**Option B — Manual script**
```python
from kiteconnect import KiteConnect
kite = KiteConnect(api_key="your_key")
print(kite.login_url())
# Open the URL, login, copy request_token from redirect URL
data = kite.generate_session("REQUEST_TOKEN", api_secret="your_secret")
print("Today's token:", data["access_token"])
```

### Step 2 — Start the system

```bash
# macOS / Linux
source venv/bin/activate
python app.py

# Windows
venv\Scripts\activate
python app.py
```

Open browser: **http://localhost:5001**

### Step 3 — On the dashboard

1. Enter API Key + Access Token → **Connect Zerodha**
2. Choose **Paper** or **Live** mode
3. Click **▶ Start Bot** (available from 9:30 AM)
4. Monitor signals, VWAP, OI, and score in real time

---

## Dashboard Controls

| Control | Function |
|---|---|
| **Connect Zerodha** | Authenticate with API key + daily access token |
| **OAuth Login** | Full OAuth flow via Zerodha login page |
| **Paper / Live toggle** | Switch trading mode (must stop bot first) |
| **▶ Start Bot** | Begin signal detection and trading |
| **⏹ Stop Bot** | Pause trading (open positions remain open) |
| **🚨 Exit ALL Positions** | Emergency square-off of all open trades |
| **Exit NIFTY / Exit BNK** | Manually close a single instrument's position |
| **⬇ Download Logs** | Download today's trading.log file |

---

## Dashboard Display

The dashboard shows in real time:

- **Market Regime** — TRENDING_UP / TRENDING_DOWN / SIDEWAYS / CHOPPY (per instrument)
- **VWAP** — current value, direction (above/below), slope per candle
- **OI Signal** — BULLISH / BEARISH / NEUTRAL with PCR
- **SAR** — current value and direction (▲/▼) — for exit reference only
- **Trade Score** — breakdown with progress bar, pass/fail
- **Signal** — entry details (pattern, tier, score, strike, premium) or exit reason
- **Position** — entry price, current LTP, SL, MTM P&L, breakeven/trailing status
- **Daily P&L** — realized total, wins, losses, win rate
- **Trade History** — last 20 closed trades with all fields
- **Live Log** — colour-coded real-time system log

---

## Run as Background Service

### macOS / Linux

```bash
python install_service.py           # install and start
python install_service.py status    # check status
python install_service.py remove    # uninstall
```

### Windows

```powershell
python install_service.py           # creates Windows Task Scheduler task
python install_service.py status
python install_service.py remove
```

---

## Troubleshooting

### `Address already in use (port 5001)`
```bash
lsof -ti :5001 | xargs kill -9   # macOS/Linux
# Windows: netstat -ano | findstr :5001 → taskkill /PID <pid> /F
```
Or change `FLASK_PORT` in `config.py`.

### `Zerodha authentication failed`
The access token expires at midnight IST daily. Regenerate via the dashboard login button.

### `KiteTicker error 1006`
Normal on pre-market. Only click **Start Bot** after 9:15 AM. The system auto-reconnects.

### `No trades firing`
Check the trade score in the dashboard. If score < 90, the gate is blocking.  
Lower `MIN_TRADE_SCORE` in `config.py` for more trades (at higher false-signal risk).

### `OI shows NEUTRAL always`
OI analysis requires live Zerodha NFO quote access. In paper mode without a live connection,  
OI defaults to NEUTRAL (score contribution = 0). Trade will still fire if pattern + VWAP alone hit 90.

---

## Disclaimer

For educational and research use only. Past performance does not guarantee future results.  
Always paper-trade thoroughly before deploying with real capital.

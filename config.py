# All the knobs for the bot in one place.

# Which strategy the bot runs:
#   "trend2x" = hold a 2x S&P 500 fund while the market is healthy, cash when it's weak
#   "stocks"  = pick up to 5 rising stocks from the watchlist (the original bot)
#   "blend"   = half and half: half "trend2x", half all-weather (see below). Chosen Nov 2026.
STRATEGY = "blend"
DAYTRADE_ON = False      # day trade with spare CASH alongside the long-term strategy (off: no tested edge, see README)
AUTO_TRADE = True        # autopilot: both strategies trade on their own (no approvals). Pause it in Momentum.
#   "daytrade" = opening range breakout on DT_SYMBOL, in and out the same day (see daytrade.py)
DT_SYMBOL = "QQQ"        # Nasdaq-100 fund: did best of the day-trading versions tested
DT_RANGE_MIN = 15        # the "opening range" = the first 15 minutes of trading
DT_LAST_ENTRY = (15, 30) # no new trade after 3:30 PM New York time
DT_EXIT = (15, 50)       # sell everything by 3:50 PM New York time (10 minutes before the close)
DT_CASH_USE = 0.98       # use 98% of cash, never borrow
DT_BREAKEVEN_R = 1.0     # once a trade is up 1x its risk, move the stop up to the buy price (research/sell_high.py)
TREND_FUND = "SSO"     # ProShares Ultra S&P 500: moves about 2x the S&P 500 each day
TREND_DAYS = 200       # "healthy" = S&P 500 above its average price over this many days
ADAPTIVE = True        # adjust how much of the 2x fund to hold to the market's mood (calm vs jumpy)
TARGET_VOL = 0.25      # aim for about this much yearly swing; calmer market -> lean in, jumpier -> pull back
MAX_LEVERAGE = 2.0     # never more than 2x the S&P 500
VOL_DAYS = 20          # "mood" = how much the S&P 500 swung over the last ~month

# Half and half ("blend"): the all-weather half holds whichever of these are rising (above their
# 200-day average), sized so calm ones get more, set once a month. research/all_weather.py tested it.
BLEND_TREND_SHARE = 0.5   # share of the account in the 2x trend half; the rest is all-weather
AW_ASSETS = {"SPY": "S&P 500 fund", "TLT": "long-term US government bonds", "IEF": "medium-term US government bonds",
             "GLD": "gold", "DBC": "commodities (oil, metals, crops)", "UUP": "US dollar"}
AW_CASH = "BIL"           # 1-3 month US Treasury bills: basically cash that earns interest
AW_TARGET_VOL = 0.15      # the all-weather half aims for about this much yearly swing (never borrows)
REBALANCE_BAND = 0.02     # leave a fund alone if it's within 2% of the account of its target
PICKS = {"NVDA": 0.10}    # your own long-term picks (share of the account): the bot tops them up after
                          # dips but never sells them. The two halves share what's left.

# Stocks/ETFs the bot is allowed to buy (stock-picking strategy). Big, heavily traded names only.
WATCHLIST = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AMZN": "Amazon",
    "GOOGL": "Alphabet (Google)", "META": "Meta (Facebook)", "JPM": "JPMorgan Chase",
    "V": "Visa", "UNH": "UnitedHealth", "XOM": "ExxonMobil", "COST": "Costco",
    "WMT": "Walmart", "JNJ": "Johnson & Johnson", "PG": "Procter & Gamble",
    "HD": "Home Depot", "LLY": "Eli Lilly",
    "QQQ": "Nasdaq-100 fund (top tech companies)", "XLF": "Financial sector fund",
    "XLE": "Energy sector fund", "XLV": "Healthcare sector fund",
}
MARKET = "SPY"  # S&P 500 fund, used as the "is the whole market healthy?" check

# Strategy settings (trend following + momentum)
SMA_FAST = 50          # short-term average price (days)
SMA_SLOW = 200         # long-term average price (days)
MOMENTUM_DAYS = 63     # about 3 months of trading days
RSI_MAX = 75           # above this a stock is "overheated"
STOP_ATR = 2.0         # safety net = this many "normal daily swings" below the best price

# Risk settings
MAX_POSITIONS = 5           # never own more than this many stocks at once
RISK_PER_TRADE = 0.02       # if the safety net is hit, lose about 2% of the account
MAX_POSITION_PCT = 0.20     # never put more than 20% of the account in one stock

# Spare cash goes into the S&P 500 fund (SPY) while the market is healthy, instead of sitting idle
PARK_IN_MARKET = True
PARK_MIN_MOVE = 0.05        # only move parked money when the change is over 5% of the account
CASH_CUSHION = 0.02         # always keep ~2% of the account in plain cash

# Settings you saved from the dashboard's What-if lab override the defaults above.
SAVED_SETTINGS = ("STOP_ATR", "RISK_PER_TRADE", "MAX_POSITIONS", "RSI_MAX", "PARK_IN_MARKET",
                  "STRATEGY", "TREND_DAYS", "ADAPTIVE", "TARGET_VOL", "DT_SYMBOL", "AUTO_TRADE", "DAYTRADE_ON")
try:
    import json as _json
    from pathlib import Path as _Path
    _f = _Path(__file__).with_name("settings.json")
    if _f.exists():
        globals().update({k: v for k, v in _json.loads(_f.read_text()).items() if k in SAVED_SETTINGS})
except Exception:
    pass  # a broken settings.json should never stop the bot; defaults are used

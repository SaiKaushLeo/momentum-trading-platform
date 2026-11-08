"""Live market data for the dashboard and the market watch (read-only, never trades).

Live prices come from Alpaca's free IEX feed; price history for charts comes from Yahoo Finance.
"""
import pandas as pd
import yfinance as yf

import config as C
from engine import load_env

_client = None


def _data_client():
    global _client
    if _client is None:
        from alpaca.data.historical import StockHistoricalDataClient
        env = load_env()
        _client = StockHistoricalDataClient(env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"])
    return _client


def snapshots(symbols):
    """{symbol: {"price", "prev_close", "change", "open", "high", "low"}} right now."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockSnapshotRequest
    snaps = _data_client().get_stock_snapshot(
        StockSnapshotRequest(symbol_or_symbols=list(symbols), feed=DataFeed.IEX))
    out = {}
    for s, x in snaps.items():
        if not x or not x.latest_trade:
            continue
        prev = x.previous_daily_bar.close if x.previous_daily_bar else None
        day = x.daily_bar
        out[s] = {"price": float(x.latest_trade.price), "prev_close": float(prev) if prev else None,
                  "change": (x.latest_trade.price / prev - 1) if prev else None,
                  "open": float(day.open) if day else None, "high": float(day.high) if day else None,
                  "low": float(day.low) if day else None}
    return out


def safety_line():
    """(S&P 500 live price, its TREND_DAYS-day average from finished days, recent yearly swing)."""
    hist = yf.download(C.MARKET, period="2y", auto_adjust=True, progress=False)["Close"].squeeze().dropna()
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    hist = hist[hist.index < today]
    avg = float(hist.iloc[-C.TREND_DAYS:].mean())
    vol = float(hist.pct_change().iloc[-C.VOL_DAYS:].std() * 252 ** 0.5)
    live = snapshots([C.MARKET]).get(C.MARKET, {}).get("price", float(hist.iloc[-1]))
    return live, avg, vol


# timeframe -> (yahoo period, bar size, label for bars)
TIMEFRAMES = {"1D": ("1d", "5m"), "5D": ("5d", "15m"), "1M": ("1mo", "60m"),
              "6M": ("6mo", "1d"), "1Y": ("1y", "1d"), "5Y": ("5y", "1wk")}


def history(symbol, timeframe, tz="America/New_York"):
    """Candles for a chart, plus 50/200-day averages on daily-or-longer timeframes."""
    period, interval = TIMEFRAMES[timeframe]
    df = yf.download(symbol, period=period, interval=interval, auto_adjust=True, progress=False,
                     prepost=False)
    if df.empty:
        return df
    df.columns = df.columns.get_level_values(0)
    if interval in ("1d", "1wk"):  # averages need longer daily history than what's displayed
        daily = yf.download(symbol, period="7y", auto_adjust=True, progress=False)
        daily.columns = daily.columns.get_level_values(0)
        avgs = pd.DataFrame({"50-day average": daily.Close.rolling(50).mean(),
                             "200-day average": daily.Close.rolling(200).mean()})
        df = df.join(avgs.reindex(df.index, method="ffill"))
    if df.index.tz is not None:
        df.index = df.index.tz_convert(tz).tz_localize(None)
    return df.dropna(subset=["Close"])


# ---------- Other exchanges (view only: the bot trades US stocks through Alpaca) ----------
# hours are local (open, close); data from Yahoo Finance is delayed about 15 minutes.
EXCHANGES = {
    "NSE": dict(label="🇮🇳 NSE (India)", tz="Asia/Kolkata", hours=((9, 15), (15, 30)), cur="₹",
                index=("^NSEI", "Nifty 50 index"), stocks={
        "RELIANCE.NS": "Reliance Industries", "TCS.NS": "Tata Consultancy Services", "HDFCBANK.NS": "HDFC Bank",
        "INFY.NS": "Infosys", "ICICIBANK.NS": "ICICI Bank", "HINDUNILVR.NS": "Hindustan Unilever",
        "ITC.NS": "ITC", "SBIN.NS": "State Bank of India", "BHARTIARTL.NS": "Bharti Airtel", "LT.NS": "Larsen & Toubro"}),
    "BSE": dict(label="🇮🇳 BSE (Bombay)", tz="Asia/Kolkata", hours=((9, 15), (15, 30)), cur="₹",
                index=("^BSESN", "Sensex index"), stocks={
        "RELIANCE.BO": "Reliance Industries", "TCS.BO": "Tata Consultancy Services", "HDFCBANK.BO": "HDFC Bank",
        "INFY.BO": "Infosys", "ICICIBANK.BO": "ICICI Bank", "HINDUNILVR.BO": "Hindustan Unilever",
        "ITC.BO": "ITC", "SBIN.BO": "State Bank of India", "BHARTIARTL.BO": "Bharti Airtel", "LT.BO": "Larsen & Toubro"}),
    "DFM": dict(label="🇦🇪 Dubai (DFM)", tz="Asia/Dubai", hours=((10, 0), (15, 0)), cur="AED ",
                index=("DFMGI.AE", "DFM General Index"), index_has_history=False, stocks={
        "EMAAR.AE": "Emaar Properties", "EMIRATESNBD.AE": "Emirates NBD", "DIB.AE": "Dubai Islamic Bank",
        "DEWA.AE": "Dubai Electricity & Water", "SALIK.AE": "Salik", "DU.AE": "du (Emirates Integrated Telecom)",
        "AIRARABIA.AE": "Air Arabia", "EMAARDEV.AE": "Emaar Development", "TECOM.AE": "TECOM Group",
        "PARKIN.AE": "Parkin", "DFM.AE": "Dubai Financial Market"}),
}


def exchange_status(key):
    """'open' or 'closed' by the exchange's local clock (weekends only; holidays not included)."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ex = EXCHANGES[key]
    now = datetime.now(ZoneInfo(ex["tz"]))
    (oh, om), (ch, cm) = ex["hours"]
    is_open = now.weekday() < 5 and (oh, om) <= (now.hour, now.minute) < (ch, cm)
    return is_open, now


def quotes_delayed(symbols):
    """{symbol: {"price", "prev_close", "change", "low", "high"}} from Yahoo (about 15 min delayed)."""
    d = yf.download(list(symbols), period="5d", interval="1d", auto_adjust=False, progress=False,
                    group_by="ticker", threads=True)
    out = {}
    for s in symbols:
        if s not in d.columns.get_level_values(0):
            continue
        x = d[s].dropna(subset=["Close"])
        if len(x) < 2:
            continue
        price, prev = float(x.Close.iloc[-1]), float(x.Close.iloc[-2])
        out[s] = {"price": price, "prev_close": prev, "change": price / prev - 1,
                  "low": float(x.Low.iloc[-1]), "high": float(x.High.iloc[-1])}
    return out

"""Downloads the free data the research uses: 5-minute prices and market news from Alpaca, 2016-today.
Saved in data/ so tests and the news AI can reuse it.   .venv/bin/python research/download.py"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import engine
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

DATA = engine.HERE / "data"
START = datetime(2016, 1, 1, tzinfo=timezone.utc)
env = engine.load_env()


def bars(symbol):
    out = DATA / f"{symbol}_5min.parquet"
    c = StockHistoricalDataClient(env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"])
    end = datetime.now(timezone.utc) - timedelta(minutes=20)  # free plan: data older than 15 minutes
    parts, t = [], START
    while t < end:
        t2 = min(t + timedelta(days=180), end)
        df = c.get_stock_bars(StockBarsRequest(symbol_or_symbols=symbol, start=t, end=t2,
                                               timeframe=TimeFrame(5, TimeFrameUnit.Minute))).df
        if not df.empty:
            parts.append(df.droplevel(0))
        t = t2
    import pandas as pd
    df = pd.concat(parts)
    df.index = df.index.tz_convert("America/New_York")
    df = df.between_time("09:30", "15:55")
    df.to_parquet(out)
    print(symbol, len(df), "bars", flush=True)


def news(symbols=("SPY", "QQQ", "DIA", "IWM")):
    out = DATA / "news.jsonl"
    nc = NewsClient(env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"])
    seen, n = set(), 0
    with open(out, "w") as f:
        t = START
        end = datetime.now(timezone.utc)
        while t < end:
            t2 = min(t + timedelta(days=30), end)
            token = None
            while True:
                req = NewsRequest(symbols=",".join(symbols), start=t, end=t2, sort="asc")  # the library pages through everything itself
                for attempt in range(5):
                    try:
                        r = nc.get_news(req)
                        break
                    except Exception as e:
                        print("retry", e, flush=True)
                        time.sleep(5)
                for a in r.data["news"]:
                    if a.id in seen:
                        continue
                    seen.add(a.id)
                    f.write(json.dumps({"t": a.created_at.isoformat(), "h": a.headline,
                                        "s": (a.summary or "")[:400], "sym": a.symbols}) + "\n")
                    n += 1
                break
            print(f"{t2:%Y-%m} {n} articles", flush=True)
            t = t2


if __name__ == "__main__":
    if "--news-only" not in sys.argv:
        for s in ("QQQ", "SPY"):
            bars(s)
    news()
    print("DONE")

"""Daily data pipeline steps: pull new 5-minute bars + news from Alpaca, merge them into data/,
and check the result before anything downstream uses it.

This is the incremental version of research/download.py. download.py grabs everything since 2016
in one go; these functions only fetch what's new since the last bar/article we already have.
Plain Python on purpose, so the Airflow DAG (dags/momentum_daily_data.py) is just wiring and the
steps can be run and tested by hand too.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("MOMENTUM_DATA_DIR", ROOT / "data"))
SYMBOLS = ["QQQ", "SPY"]
NEWS_SYMBOLS = ["SPY", "QQQ", "DIA", "IWM"]
START = datetime(2016, 1, 1, tzinfo=timezone.utc)  # same start as download.py
ET = "America/New_York"
FREE_PLAN_DELAY = timedelta(minutes=20)  # free plan: data older than 15 minutes
MAX_MISSING_BARS = 5  # IEX sometimes skips a bar or two on slow days, more than this = something broke
OHLCV = ["open", "high", "low", "close", "volume"]


class DataQualityError(Exception):
    pass


def keys():
    """Reads the Alpaca keys out of .env (read only, same format engine.load_env uses)."""
    env = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"]


def bars_file(symbol):
    return DATA / f"{symbol}_5min.parquet"


def stage_dir(ds):
    d = DATA / "staging" / ds
    d.mkdir(parents=True, exist_ok=True)
    return d


def window_end(run_end):
    """Can't ask for anything newer than ~15 min on the free plan, even if the run says so."""
    now = datetime.now(timezone.utc) - FREE_PLAN_DELAY
    return min(pd.Timestamp(run_end).tz_convert("UTC").to_pydatetime(), now)


# ---------- extract ----------

def extract_bars(symbol, run_end, ds):
    """Downloads bars from the start of the last day we have (so a half-finished day gets
    redone) up to the end of this run. Saves them to data/staging/<ds>/ and returns a summary."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    f = bars_file(symbol)
    if f.exists():
        last = pd.read_parquet(f, columns=["close"]).index.max()
        start = last.normalize().tz_convert("UTC").to_pydatetime()
    else:
        start = START
    end = window_end(run_end)

    parts, t = [], start
    c = StockHistoricalDataClient(*keys())
    while t < end:
        t2 = min(t + timedelta(days=180), end)
        df = c.get_stock_bars(StockBarsRequest(symbol_or_symbols=symbol, start=t, end=t2,
                                               timeframe=TimeFrame(5, TimeFrameUnit.Minute))).df
        if not df.empty:
            parts.append(df.droplevel(0))
        t = t2

    if parts:
        df = pd.concat(parts)
        df.index = df.index.tz_convert(ET)
        df = df.between_time("09:30", "15:55")
    else:
        df = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "trade_count", "vwap"])
    out = stage_dir(ds) / f"{symbol}_5min.parquet"
    df.to_parquet(out)
    print(f"{symbol}: {len(df)} bars from {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC -> {out}", flush=True)
    return {"symbol": symbol, "path": str(out), "start": start.isoformat(), "end": end.isoformat(), "rows": len(df)}


def last_news_time():
    f = DATA / "news.jsonl"
    if not f.exists() or f.stat().st_size == 0:
        return START
    with open(f, "rb") as fh:  # only need the last line, file is ~18 MB
        fh.seek(max(0, f.stat().st_size - 20000))
        last = fh.read().decode("utf-8", "ignore").strip().splitlines()[-1]
    return datetime.fromisoformat(json.loads(last)["t"])


def extract_news(run_end, ds):
    from alpaca.data.historical.news import NewsClient
    from alpaca.data.requests import NewsRequest

    start, end = last_news_time(), window_end(run_end)
    nc = NewsClient(*keys())
    seen, rows, t = set(), [], start
    while t < end:
        t2 = min(t + timedelta(days=30), end)
        r = nc.get_news(NewsRequest(symbols=",".join(NEWS_SYMBOLS), start=t, end=t2, sort="asc"))
        for a in r.data["news"]:
            if a.id in seen or a.created_at <= start:
                continue
            seen.add(a.id)
            rows.append({"t": a.created_at.isoformat(), "h": a.headline,
                         "s": (a.summary or "")[:400], "sym": a.symbols})
        t = t2
    out = stage_dir(ds) / "news.jsonl"
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"news: {len(rows)} new articles since {start:%Y-%m-%d %H:%M} -> {out}", flush=True)
    return {"path": str(out), "start": start.isoformat(), "end": end.isoformat(), "rows": len(rows)}


# ---------- load ----------

def load_bars(info):
    """Swaps in the staged days: keeps every old day that isn't in the new batch, adds the new
    batch, writes to a temp file and renames it (so a crash can't leave half a file).
    Running it twice gives the same result."""
    f, staged = bars_file(info["symbol"]), pd.read_parquet(info["path"])
    old = pd.read_parquet(f) if f.exists() else staged.iloc[0:0]
    info = dict(info, rows_before=len(old))
    if len(staged):
        new_days = staged.index.normalize().unique()
        old = old[~old.index.normalize().isin(new_days)]
        merged = pd.concat([old, staged]).sort_index()
        tmp = f.with_suffix(".tmp.parquet")
        merged.to_parquet(tmp)
        os.replace(tmp, f)
        info["rows_after"] = len(merged)
    else:
        info["rows_after"] = len(old)
    print(f"{info['symbol']}: {info['rows_before']} -> {info['rows_after']} bars in {f.name}", flush=True)
    return info


def load_news(info):
    """Appends staged articles newer than the last one in news.jsonl (re-checked here so a retry
    doesn't add them twice)."""
    last = last_news_time()
    rows = [json.loads(l) for l in open(info["path"])]
    rows = [r for r in rows if datetime.fromisoformat(r["t"]) > last]
    with open(DATA / "news.jsonl", "a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"news: appended {len(rows)} articles", flush=True)
    return dict(info, appended=len(rows))


# ---------- data quality ----------

def trading_sessions(start, end):
    """Market calendar from Alpaca (read only). Returns [(open, close)] in ET for sessions that
    were fully over by `end`, so a run in the middle of the day doesn't count today."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetCalendarRequest

    s, e = pd.Timestamp(start).tz_convert(ET), pd.Timestamp(end).tz_convert(ET)
    cal = TradingClient(*keys(), paper=True).get_calendar(GetCalendarRequest(start=s.date(), end=e.date()))
    out = []
    for d in cal:
        o, c = pd.Timestamp(d.open).tz_localize(ET), pd.Timestamp(d.close).tz_localize(ET)
        if c <= e:
            out.append((o, c))
    return out


def check_bars(df, symbol, sessions, rows_before=None, rows_new=None):
    """Returns a list of problems (empty list = good). Whole-file checks plus a missing-bar check
    for each trading session in this run's window."""
    bad = []
    dups = int(df.index.duplicated().sum())
    if dups:
        bad.append(f"{symbol}: {dups} duplicate bars (same timestamp twice)")
    if not df.index.is_monotonic_increasing:
        bad.append(f"{symbol}: bars are out of order")
    nulls = int(df[OHLCV].isna().sum().sum())
    if nulls:
        bad.append(f"{symbol}: {nulls} empty price/volume values")
    weird = (df.high < df[["open", "close"]].max(axis=1)) | (df.low > df[["open", "close"]].min(axis=1)) | (df.low <= 0)
    if weird.sum():
        bad.append(f"{symbol}: {int(weird.sum())} bars where high/low don't make sense")
    if rows_before is not None and len(df) < rows_before:
        bad.append(f"{symbol}: file shrank from {rows_before} to {len(df)} rows")
    if sessions and rows_new == 0:
        bad.append(f"{symbol}: {len(sessions)} trading day(s) in the window but no new bars came back")

    have = set(df.index)
    for o, c in sessions:
        expected = pd.date_range(o, c - pd.Timedelta(minutes=5), freq="5min")
        missing = [t for t in expected if t not in have]
        if len(missing) > MAX_MISSING_BARS:
            bad.append(f"{symbol} {o:%Y-%m-%d}: missing {len(missing)} of {len(expected)} bars "
                       f"(first gap {missing[0]:%H:%M})")
        elif missing:
            print(f"warning {symbol} {o:%Y-%m-%d}: missing {len(missing)} bar(s), under the limit", flush=True)
    return bad


def check_news(info):
    bad = []
    rows = [json.loads(l) for l in open(info["path"])]
    if any(not r["h"].strip() for r in rows):
        bad.append("news: some articles have an empty headline")
    keys_ = [(r["t"], r["h"]) for r in rows]
    if len(keys_) != len(set(keys_)):
        bad.append(f"news: {len(keys_) - len(set(keys_))} duplicate articles in this batch")
    return bad


def quality_check(bar_infos, news_info):
    """Runs every check and raises DataQualityError with the full list if anything is off."""
    bad, report = [], []
    sessions = trading_sessions(min(i["start"] for i in bar_infos), max(i["end"] for i in bar_infos))
    for i in bar_infos:
        df = pd.read_parquet(bars_file(i["symbol"]))
        bad += check_bars(df, i["symbol"], sessions, i.get("rows_before"), i["rows"])
        report.append(f"{i['symbol']}: {len(df)} bars total, {i['rows']} pulled this run, "
                      f"last bar {df.index.max():%Y-%m-%d %H:%M}")
    if news_info:
        bad += check_news(news_info)
        report.append(f"news: {news_info['rows']} new articles")
    report.append(f"trading days checked for missing bars: {[f'{o:%m-%d}' for o, _ in sessions]}")
    print("\n".join(report), flush=True)
    if bad:
        raise DataQualityError("data quality check failed:\n  - " + "\n  - ".join(bad))
    print("data quality: all checks passed", flush=True)
    return report

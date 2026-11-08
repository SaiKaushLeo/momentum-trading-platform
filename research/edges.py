"""Other known trading patterns, tested honestly (2016-today on 5-minute prices, 1999-today on daily).
  1. Intraday momentum: the first half hour's move tends to continue in the last half hour.
  2. Overnight drift: much of the market's gain happens between the close and the next open.
  3. Buy the dip in an uptrend: after a sharp 2-day drop while above the 200-day average."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import intraday as I


def bar(g, h, m, col="open"):
    x = g[(g.index.hour == h) & (g.index.minute == m)]
    return float(x[col].iloc[0]) if len(x) else np.nan


def intraday_momentum(sym, both=True):
    df = I.load(sym)
    out = {}
    for d, g, prev in I.days(df):
        first = bar(g, 10, 0) / prev - 1      # previous close -> 10:00 AM
        a, b = bar(g, 15, 30), float(g.close.iloc[-1])  # 3:30 PM -> close
        if np.isnan(a) or np.isnan(first) or abs(first) < 0.0:
            continue
        r = b / a - 1
        out[d] = (r if first > 0 else (-r if both else 0.0)) - 0.0002 * (first > 0 or both)
    return pd.Series(out)


def overnight(sym):
    df = I.load(sym)
    g = df.groupby("day")
    o, c = g.open.first(), g.close.last()
    r = o.shift(-1) / c - 1 - 0.0002
    return r.dropna()


def dip_buy(sym, start="1999-01-01"):
    import yfinance as yf
    d = yf.download(sym, start=start, auto_adjust=True, progress=False)
    c, o = d["Close"].squeeze(), d["Open"].squeeze()
    delta = c.diff()
    up, dn = delta.clip(lower=0).rolling(2).mean(), (-delta.clip(upper=0)).rolling(2).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, 1e-9))
    above = c > c.rolling(200).mean()
    ma5 = c.rolling(5).mean()
    pos, out = 0, []
    for i in range(201, len(c) - 1):
        if pos == 0 and above.iloc[i] and rsi.iloc[i] < 10:
            pos, entry = 1, o.iloc[i + 1]           # buy at next open
            out.append(0.0)
            continue
        if pos == 1 and c.iloc[i] > ma5.iloc[i]:
            out.append(o.iloc[i + 1] / c.iloc[i - 1] - 1 if False else o.iloc[i + 1] / c.iloc[i] - 1 + (c.iloc[i] / c.iloc[i - 1] - 1) - 0.0002)
            pos = 0
            continue
        out.append(c.iloc[i] / c.iloc[i - 1] - 1 if pos else 0.0)
    s = pd.Series(out, index=c.index[201:len(c) - 1])
    return s, c


if __name__ == "__main__":
    print(I.HEADER)
    for sym in ("SPY", "QQQ"):
        I.summary(f"{sym} intraday momentum, both ways", intraday_momentum(sym))
        I.summary(f"{sym} intraday momentum, buy only", intraday_momentum(sym, both=False))
        I.summary(f"{sym} overnight only (buy close, sell open)", overnight(sym), 1.0)
    for sym in ("SPY", "QQQ"):
        s, c = dip_buy(sym)
        s.index = s.index.date
        print(f"\n{sym} dip buying, daily 1999-today (halves 1999-2012 / 2013-2026 below):")
        d = s
        eq = (1 + pd.Series(d.values, index=pd.to_datetime(d.index))).cumprod()
        yrs = len(d) / 252
        for a, b in (("1999", "2012"), ("2013", "2026")):
            x = pd.Series(d.values, index=pd.to_datetime(d.index))[a:b]
            print(f"   {a}-{b}: {(1 + x).prod() ** (252 / len(x)) - 1:.1%} a year, in the market {np.mean(x != 0):.0%} of days")
        print(f"   all: {eq.iloc[-1] ** (1 / yrs) - 1:.1%}/yr, worst {(eq / eq.cummax() - 1).min():.0%}; "
              f"holding: {(c.iloc[-1] / c.iloc[201]) ** (1 / yrs) - 1:.1%}/yr")

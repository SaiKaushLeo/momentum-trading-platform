"""Day-trading tests on 5-minute QQQ prices (2016-today), with costs. Fills are at the NEXT bar's open
after a signal, stops fill at the stop price or worse, and everything is sold by 3:50 PM.
'Short' trades are done the way the bot would: buying PSQ (a fund that goes UP when QQQ goes down)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
COST_LONG, COST_SHORT = 0.0001, 0.0003  # per side; PSQ has a wider spread


def load(symbol="QQQ"):
    df = pd.read_parquet(DATA / f"{symbol}_5min.parquet")
    df["day"] = df.index.date
    return df


def days(df):
    """Yields (date, bars of that day as numpy arrays, previous close)."""
    prev = None
    for d, g in df.groupby("day", sort=True):
        if len(g) < 70:  # half days / missing data
            prev = float(g.close.iloc[-1])
            continue
        if prev is not None:
            yield d, g, prev
        prev = float(g.close.iloc[-1])


def trade(g, prev, side, i_fill, stop, target=None):
    """Simulate one trade from the open of bar i_fill. side +1 = QQQ long, -1 = PSQ (bet against QQQ).
    Returns the % result after costs."""
    o, h, l = g.open.values, g.high.values, g.low.values
    times = g.index
    entry = o[i_fill]
    exit_px = None
    for i in range(i_fill, len(g)):
        if times[i].hour == 15 and times[i].minute >= 50:
            exit_px = o[i]
            break
        if side > 0:
            if l[i] <= stop:
                exit_px = min(stop, o[i]) if i > i_fill else min(stop, entry)
                break
            if target and h[i] >= target:
                exit_px = max(target, o[i])
                break
        else:
            if h[i] >= stop:
                exit_px = max(stop, o[i]) if i > i_fill else max(stop, entry)
                break
            if target and l[i] <= target:
                exit_px = min(target, o[i])
                break
    if exit_px is None:
        exit_px = g.close.values[-1]
    if side > 0:
        return exit_px / entry - 1 - 2 * COST_LONG
    psq = lambda q: 2 - q / prev  # PSQ's value relative to its previous close
    return psq(exit_px) / psq(entry) - 1 - 2 * COST_SHORT


def orb(df, minutes=15, long=True, short=False, direction=None):
    """Opening range breakout. direction: optional {date: "L", "S", "LS" or ""} = sides allowed that day."""
    n = minutes // 5
    out = {}
    for d, g, prev in days(df):
        hi, lo = g.high.values[:n].max(), g.low.values[:n].min()
        c, times = g.close.values, g.index
        allow = direction.get(d, "LS") if direction is not None else "LS"
        res = 0.0
        for i in range(n, len(g) - 1):
            if (times[i].hour, times[i].minute) >= (15, 30):
                break
            ok_long = long and "L" in allow
            ok_short = short and "S" in allow
            if ok_long and c[i] > hi:
                res = trade(g, prev, +1, i + 1, lo)
                break
            if ok_short and c[i] < lo:
                res = trade(g, prev, -1, i + 1, hi)
                break
        out[d] = res
    return pd.Series(out)


def first_candle(df, atr_stop=None, target_r=10):
    """'5-minute ORB' from published research: trade in the direction of the first 5-minute candle,
    stop at the other end of that candle, target 10x the risk, else out at the close."""
    out = {}
    for d, g, prev in days(df):
        o0, c0, h0, l0 = g.open.values[0], g.close.values[0], g.high.values[0], g.low.values[0]
        if c0 == o0:
            out[d] = 0.0
            continue
        side = 1 if c0 > o0 else -1
        entry = g.open.values[1]
        stop = l0 if side > 0 else h0
        risk = abs(entry - stop)
        if risk <= 0:
            out[d] = 0.0
            continue
        out[d] = trade(g, prev, side, 1, stop, entry + side * target_r * risk)
    return pd.Series(out)


def summary(name, daily, use=0.98):
    d = daily * use
    d.index = pd.to_datetime(d.index)
    eq = (1 + d).cumprod()
    yrs = (d.index[-1] - d.index[0]).days / 365.25
    traded = d[d != 0]
    halves = []
    for a, b in (("2016", "2020"), ("2021", "2026")):
        x = d[a:b]
        halves.append((1 + x).prod() ** (365.25 / max((x.index[-1] - x.index[0]).days, 1)) - 1)
    dd = (eq / eq.cummax() - 1).min()
    print(f"{name:44}{eq.iloc[-1] ** (1 / yrs) - 1:>8.1%}{halves[0]:>10.1%}{halves[1]:>10.1%}"
          f"{dd:>8.0%}{(traded > 0).mean():>7.0%}{len(traded) / len(d):>8.0%}", flush=True)
    return d


HEADER = f"{'':44}{'per yr':>8}{'2016-20':>10}{'2021-26':>10}{'worst':>8}{'won':>7}{'traded':>8}"

if __name__ == "__main__":
    df = load()
    print(HEADER)
    hold = df.groupby("day").close.last()
    hold = hold.pct_change().fillna(0)
    summary("Just hold QQQ", hold, 1.0)
    summary("ORB 15 min, buy only (what the bot does now)", orb(df, 15))
    summary("ORB 15 min, buy AND bet against", orb(df, 15, short=True))
    summary("ORB 30 min, buy AND bet against", orb(df, 30, short=True))
    summary("ORB 5 min, buy AND bet against", orb(df, 5, short=True))
    summary("First-candle direction, 10R target", first_candle(df))

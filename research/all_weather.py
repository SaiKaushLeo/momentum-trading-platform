"""Can the bot make money in bear markets too? Multi-asset trend following.
When the S&P 500 is weak, instead of cash, hold whatever is trending UP among assets that often rise when
stocks fall: long-term government bonds (TLT), medium bonds (IEF), gold (GLD), commodities (DBC), the US dollar
(UUP). Decided at each month's end (or when the stock signal flips), using only past prices.
Pre-set bar to adopt it: make money in the bear years (2008, 2022) AND not lower the overall result."""
import numpy as np
import pandas as pd
import yfinance as yf

SAFE = ["TLT", "IEF", "GLD", "DBC", "UUP"]
px = yf.download(["SPY", "BIL"] + SAFE, start="2006-01-01", auto_adjust=True, progress=False)["Close"].dropna()
r = px.pct_change().fillna(0)
spy = px.SPY
healthy = spy > spy.rolling(200).mean()
vol = r.SPY.rolling(20).std() * np.sqrt(252)
L = ((0.25 / vol * 4).round() / 4).clip(0.25, 2.0)
fee = 0.009 / 252
month_end = px.index.to_series().dt.month != px.index.to_series().shift(-1).dt.month


def pick(i, top, lookback):
    """Best `top` safe assets that are above their 200-day average and up over `lookback` days."""
    t = px.index[i]
    mom = px[SAFE].iloc[i] / px[SAFE].iloc[i - lookback] - 1
    ok = [s for s in SAFE if px[s].iloc[i] > px[s].iloc[i - 200:i].mean() and mom[s] > mom.get("BIL", 0)]
    return sorted(ok, key=lambda s: -mom[s])[:top]


def run(mode, top=2, lookback=126):
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    cur, picks = None, []
    for i in range(260, len(px)):
        t = px.index[i]
        if healthy.iloc[i]:
            cur = ("stocks", L.iloc[i])
        else:
            if mode == "cash":
                cur = ("cash",)
            elif cur is None or cur[0] != "safe" or month_end.iloc[i]:
                picks = pick(i, top, lookback)
                cur = ("safe", tuple(picks))
        if cur[0] == "stocks":
            w.loc[t, "SPY"] = cur[1]
        elif cur[0] == "cash" or not cur[1]:
            w.loc[t, "BIL"] = 1.0
        else:
            for s in cur[1]:
                w.loc[t, s] = 1 / len(cur[1])
    w = w.shift(1).fillna(0)  # decided at yesterday's close
    lev_cost = (w.SPY - 1).clip(lower=0) * fee
    turn = w.diff().abs().sum(1).fillna(0) * 0.0005
    d = (w * r).sum(1) - lev_cost - turn
    return d[px.index[261]:]


def show(name, d):
    eq = (1 + d).cumprod()
    yrs = len(d) / 252
    yr = (1 + d).groupby(d.index.year).prod() - 1
    halves = [(1 + d[a:b]).prod() ** (252 / len(d[a:b])) - 1 for a, b in (("2007", "2016"), ("2017", "2026"))]
    print(f"{name:34}{eq.iloc[-1] ** (1 / yrs) - 1:>7.1%}{(eq / eq.cummax() - 1).min():>7.0%}"
          f"{halves[0]:>9.1%}{halves[1]:>9.1%}" + "".join(f"{yr.get(y, np.nan):>7.0%}" for y in BEAR)
          + f"{(yr < 0).sum():>5}/{len(yr)}")


BEAR = [2008, 2011, 2015, 2018, 2020, 2022]
print(f"{'':34}{'per yr':>7}{'worst':>7}{'07-16':>9}{'17-26':>9}" + "".join(f"{y:>7}" for y in BEAR) + "  losing yrs")
show("Just hold S&P 500", r.SPY[px.index[261]:])
show("Now: 2x trend, cash when weak", run("cash"))
for top in (1, 2, 3):
    for lb in (63, 126, 252):
        show(f"2x trend, best {top} safe assets ({lb}d)", run("safe", top, lb))


def balanced(assets, target=0.15, cap=2.0):
    """Every asset held while above its 200-day average, sized by inverse swing, total scaled to `target` swing."""
    w = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    vols = r.rolling(60).std() * np.sqrt(252)
    for i in range(260, len(px)):
        t = px.index[i]
        if not month_end.iloc[i] and i > 260:
            w.iloc[i] = w.iloc[i - 1]
            continue
        up = [s for s in assets if px[s].iloc[i] > px[s].iloc[i - 200:i].mean()]
        if not up:
            w.loc[t, "BIL"] = 1.0
            continue
        raw = pd.Series({s: 1 / vols[s].iloc[i] for s in up})
        raw /= raw.sum()
        port_vol = np.sqrt(raw @ (r[up].iloc[i - 60:i].cov() * 252) @ raw)
        k = min(cap, target / port_vol)
        for s in up:
            w.loc[t, s] = raw[s] * k
        w.loc[t, "BIL"] = max(0.0, 1 - k)
    w = w.shift(1).fillna(0)
    borrow = (w.drop(columns="BIL").sum(1) - 1).clip(lower=0) * (r.BIL + 0.01 / 252)  # leverage isn't free
    turn = w.diff().abs().sum(1).fillna(0) * 0.0005
    return ((w * r).sum(1) - borrow - turn)[px.index[261]:]


if __name__ == "__main__":
    print()
    show("Balanced trend, all 6 (15% swing)", balanced(["SPY"] + SAFE))
    show("Balanced trend, all 6 (10% swing)", balanced(["SPY"] + SAFE, 0.10))

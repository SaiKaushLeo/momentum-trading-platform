"""Replays the strategy over past years to see how it would have done.

Run:  .venv/bin/python backtest.py [years]
"""
import sys

import pandas as pd

import config as C
from strategy import load_history, plan, plan_trend

SLIPPAGE = 0.0005  # assume we pay a little worse than the open price


_cache = {}
last_curve = None  # the most recent run's daily account values (for the dashboard chart)


def run(years=20, start_cash=100_000, quiet=False, **overrides):
    """overrides: any config setting, e.g. STOP_ATR=2.0, to test a 'what if'."""
    saved = {k: getattr(C, k) for k in overrides}
    for k, v in overrides.items():
        setattr(C, k, v)
    try:
        return _run(years, start_cash, quiet)
    finally:
        for k, v in saved.items():
            setattr(C, k, v)


def _run(years, start_cash, quiet):
    if C.STRATEGY == "blend":
        return _run_blend(years, start_cash, quiet)
    symbols = list(C.WATCHLIST) + [C.MARKET, C.TREND_FUND]
    if years not in _cache:
        _cache[years] = load_history(symbols, years)
    data = _cache[years]
    if C.STRATEGY == "trend2x":
        return _run_trend(data, start_cash, quiet)
    spy = data[C.MARKET]
    warmup = C.SMA_SLOW + 5
    dates = spy.index[warmup:]

    cash, holdings, curve, trades, park = start_cash, {}, [], [], 0
    for i, t in enumerate(dates[:-1]):
        rows = {s: d.loc[t] for s, d in data.items()
                if s in C.WATCHLIST and t in d.index and d.loc[t].notna().all()}
        for s, h in holdings.items():
            if s in rows:
                h["high"] = max(h["high"], rows[s].Close)
        equity = (cash + park * spy.loc[t].Close
                  + sum(h["qty"] * rows[s].Close for s, h in holdings.items() if s in rows))
        curve.append(equity)

        _, actions, _ = plan(rows, spy.loc[t], holdings, equity, cash, park)
        nxt = dates[i + 1]  # orders fill at the next day's open
        for a in actions:
            s = a["symbol"]
            if nxt not in data[s].index:
                continue
            px = data[s].loc[nxt].Open
            if a.get("kind") == "park":  # spare cash in/out of the S&P 500 fund (not counted as a trade)
                if a["side"] == "SELL":
                    cash += a["qty"] * px * (1 - SLIPPAGE)
                    park -= a["qty"]
                else:
                    qty = min(a["qty"], int(cash // (px * (1 + SLIPPAGE))))
                    cash -= qty * px * (1 + SLIPPAGE)
                    park += qty
                continue
            if a["side"] == "SELL":
                px *= 1 - SLIPPAGE
                h = holdings.pop(s)
                cash += h["qty"] * px
                trades.append(px / h["entry"] - 1)
            else:
                px *= 1 + SLIPPAGE
                if a["qty"] * px > cash:
                    continue
                cash -= a["qty"] * px
                holdings[s] = {"qty": a["qty"], "high": px, "entry": px, "stop_atr": a["stop_atr"]}

    global last_curve
    spy_close = spy.loc[dates[0]:dates[-2]].Close
    last_curve = pd.DataFrame({"Bot": curve, "Just hold S&P 500": start_cash * spy_close / spy_close.iloc[0]},
                              index=dates[:-1])
    return report(curve, trades, spy_close, start_cash, quiet)


def _run_trend(data, start_cash, quiet):
    spy, fund = data[C.MARKET], data[C.TREND_FUND]
    avg = spy.Close.rolling(C.TREND_DAYS).mean()
    vol = spy.Close.pct_change().rolling(C.VOL_DAYS).std() * (252 ** 0.5)
    dates = spy.index.intersection(fund.index)
    dates = dates[dates >= avg.dropna().index[0]]
    syms = {C.TREND_FUND: fund, C.MARKET: spy}
    cash, qty, entry, curve, trades = start_cash, {s: 0 for s in syms}, {}, [], []
    for i, t in enumerate(dates[:-1]):
        prices = {s: d.Close[t] for s, d in syms.items()}
        equity = cash + sum(qty[s] * prices[s] for s in syms)
        curve.append(equity)
        _, actions, _ = plan_trend(spy.Close[t], avg[t], vol[t], prices, qty, {}, equity, cash)
        nxt = dates[i + 1]  # orders fill at the next day's open
        for a in actions:
            s, px = a["symbol"], syms[a["symbol"]].Open[nxt]
            if a["side"] == "SELL":
                cash += a["qty"] * px * (1 - SLIPPAGE)
                trades.append(px / entry[s] - 1)
                qty[s] -= a["qty"]
            else:
                n = min(a["qty"], int(cash // (px * (1 + SLIPPAGE))))
                entry[s] = px if not qty[s] else (entry[s] * qty[s] + px * n) / (qty[s] + n)
                cash -= n * px * (1 + SLIPPAGE)
                qty[s] += n
    global last_curve
    spy_close = spy.Close[dates[0]:dates[-2]]
    last_curve = pd.DataFrame({"Bot": curve, "Just hold S&P 500": start_cash * spy_close / spy_close.iloc[0]},
                              index=dates[:-1])
    return report(curve, trades, spy_close, start_cash, quiet)


def _run_blend(years, start_cash, quiet):
    """Half and half, replayed with the live bot's own planning code. Orders fill at the next day's open.
    History starts in 2007, when the newest of its funds began trading."""
    import engine
    from strategy import blend_targets, plan_blend
    key = ("blend", years)
    if key not in _cache:
        _cache[key] = engine.blend_closes(years)
    closes, opens = _cache[key]
    dates = closes.index
    cash, qty, entry, curve, trades = start_cash, {s: 0 for s in closes}, {}, [], []
    first = 262
    for i in range(first, len(dates) - 1):
        prices = closes.iloc[i].to_dict()
        equity = cash + sum(qty[s] * prices[s] for s in qty)
        curve.append(equity)
        w, info = blend_targets(closes, i)
        _, actions, _ = plan_blend(w, info, prices, qty, {}, equity)
        nxt = opens.iloc[i + 1]
        for a in actions:
            s, px = a["symbol"], nxt[a["symbol"]]
            if a["side"] == "SELL":
                cash += a["qty"] * px * (1 - SLIPPAGE)
                trades.append(px / entry[s] - 1)
                qty[s] -= a["qty"]
            else:
                n = min(a["qty"], int(cash // (px * (1 + SLIPPAGE))))
                entry[s] = px if not qty[s] else (entry[s] * qty[s] + px * n) / (qty[s] + n)
                cash -= n * px * (1 + SLIPPAGE)
                qty[s] += n
    global last_curve
    spy_close = closes[C.MARKET].iloc[first:len(dates) - 1]
    last_curve = pd.DataFrame({"Bot": curve, "Just hold S&P 500": start_cash * spy_close / spy_close.iloc[0]},
                              index=dates[first:len(dates) - 1])
    return report(curve, trades, spy_close, start_cash, quiet)


def max_drawdown(values):
    peak, worst = values[0], 0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def report(curve, trades, spy_close, start_cash, quiet=False):
    yrs = len(curve) / 252
    end = curve[-1]
    spy_end = start_cash * spy_close.iloc[-1] / spy_close.iloc[0]
    wins = [t for t in trades if t > 0]
    stats = dict(years=round(yrs, 1), ending_value=round(end),
                 yearly_growth=f"{(end / start_cash) ** (1 / yrs) - 1:.1%}",
                 worst_drop=f"{max_drawdown(curve):.1%}", trades=len(trades),
                 win_rate=f"{len(wins) / max(len(trades), 1):.0%}",
                 spy_yearly_growth=f"{(spy_end / start_cash) ** (1 / yrs) - 1:.1%}",
                 spy_worst_drop=f"{max_drawdown(list(spy_close)):.1%}")
    if quiet:
        return stats
    print(f"\nBacktest over {yrs:.1f} years, starting with ${start_cash:,.0f}\n")
    print(f"{'':28}{'Bot':>14}{'Just buy SPY':>16}")
    print(f"{'Ending value':28}{end:>14,.0f}{spy_end:>16,.0f}")
    print(f"{'Yearly growth (CAGR)':28}{(end / start_cash) ** (1 / yrs) - 1:>14.1%}"
          f"{(spy_end / start_cash) ** (1 / yrs) - 1:>16.1%}")
    print(f"{'Worst drop from a peak':28}{max_drawdown(curve):>14.1%}"
          f"{max_drawdown(list(spy_close)):>16.1%}")
    print(f"\nTrades: {len(trades)}, winners: {len(wins) / max(len(trades), 1):.0%}, "
          f"avg winner {sum(wins) / max(len(wins), 1):+.1%}, "
          f"avg loser {sum(t for t in trades if t <= 0) / max(len(trades) - len(wins), 1):+.1%}")
    if C.STRATEGY == "trend2x":
        print("\nCaution: past results don't guarantee the future. The edge over the S&P 500 is small "
              "(about 1-1.5%/yr) and it lost to the S&P 500 in roughly half of the years.")
    elif C.STRATEGY == "blend":
        print("\nHalf and half: half the 2x trend, half all-weather (bonds, gold, commodities, dollar, stocks:"
              " whichever are rising). Aims for steadier years rather than the most growth.")
    else:
        print("\nCaution: the watchlist is today's big winners, so the past looks better than it "
              "really was (hindsight). Treat these numbers as an optimistic upper bound.")
    return stats


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 20)

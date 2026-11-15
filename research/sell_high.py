"""Would 'selling at a high' help the day trade? Same opening range breakout (buy when a 5-minute candle
closes above the first 15 minutes' high, stop at its low), but different ways out:
  - profit target: sell once QQQ is up k times the risk (risk = entry minus the stop)
  - trailing stop: once in the trade, the stop follows the highest price, k times the risk below it
  - breakeven: move the stop to the entry price once the trade is up 1x the risk
Adopted only if it beats 'hold to 3:50 PM' in BOTH 2016-20 and 2021-26."""
import pandas as pd

import intraday as I


def orb_exit(df, target_r=None, trail_r=None, breakeven_r=None, minutes=15):
    n = minutes // 5
    out = {}
    for d, g, prev in I.days(df):
        hi, lo = g.high.values[:n].max(), g.low.values[:n].min()
        o, h, l, c, t = g.open.values, g.high.values, g.low.values, g.close.values, g.index
        res = 0.0
        for i in range(n, len(g) - 1):
            if (t[i].hour, t[i].minute) >= (15, 30):
                break
            if c[i] > hi:
                k = i + 1
                entry, stop = o[k], lo
                risk = entry - stop
                if risk <= 0:
                    break
                target = entry + target_r * risk if target_r else None
                peak, exit_px = entry, None
                for j in range(k, len(g)):
                    if t[j].hour == 15 and t[j].minute >= 50:
                        exit_px = o[j]
                        break
                    if l[j] <= stop:
                        exit_px = min(stop, o[j]) if j > k else min(stop, entry)
                        break
                    if target and h[j] >= target:
                        exit_px = max(target, o[j])
                        break
                    peak = max(peak, h[j])  # stops move only after the bar is done
                    if trail_r:
                        stop = max(stop, peak - trail_r * risk)
                    if breakeven_r and peak >= entry + breakeven_r * risk:
                        stop = max(stop, entry)
                if exit_px is None:
                    exit_px = c[-1]
                res = exit_px / entry - 1 - 2 * I.COST_LONG
                break
        out[d] = res
    return pd.Series(out)


if __name__ == "__main__":
    df = I.load()
    print(I.HEADER)
    I.summary("Just hold QQQ", df.groupby("day").close.last().pct_change().fillna(0), 1.0)
    I.summary("Hold to 3:50 PM (what the bot does now)", orb_exit(df))
    for k in (0.5, 1, 1.5, 2, 3):
        I.summary(f"Sell at a profit of {k}x the risk", orb_exit(df, target_r=k))
    for k in (0.5, 1, 1.5, 2):
        I.summary(f"Trailing stop {k}x the risk below the high", orb_exit(df, trail_r=k))
    I.summary("Stop to breakeven once up 1x the risk", orb_exit(df, breakeven_r=1))
    I.summary("Breakeven at 1x + sell at 2x", orb_exit(df, target_r=2, breakeven_r=1))

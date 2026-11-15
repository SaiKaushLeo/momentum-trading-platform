"""The trading rules, plus plain-English explanations for every decision.

Used by both bot.py (real paper trading) and backtest.py (testing on old data),
so the backtest is testing exactly the same logic the bot runs.
"""
import math

import numpy as np

import pandas as pd
import yfinance as yf

import config as C


def load_history(symbols, years):
    """Daily price history for each symbol, with indicators added."""
    raw = yf.download(symbols, period=f"{years}y", auto_adjust=True,
                      group_by="ticker", progress=False, threads=True)
    out = {}
    for s in symbols:
        df = raw[s].dropna()
        if len(df) > C.SMA_SLOW:
            out[s] = add_indicators(df)
    return out


def add_indicators(df):
    d = df.copy()
    c = d["Close"]
    d["sma_fast"] = c.rolling(C.SMA_FAST).mean()
    d["sma_slow"] = c.rolling(C.SMA_SLOW).mean()
    d["mom"] = c.pct_change(C.MOMENTUM_DAYS)
    # RSI: 0-100 score of how hard the price has been rising lately
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + gain / loss)
    # ATR: the stock's typical daily price swing in dollars
    prev = c.shift()
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - prev).abs(),
                    (d["Low"] - prev).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return d


def name(sym):
    if sym == C.TREND_FUND:
        return "2x S&P 500 fund"
    if sym == C.MARKET:
        return "S&P 500 fund"
    if sym == C.AW_CASH:
        return "US Treasury bills (cash that earns interest)"
    return C.AW_ASSETS.get(sym) or C.WATCHLIST.get(sym, sym)


def market_check(spy):
    ok = spy.Close > spy.sma_slow
    if ok:
        text = (f"Overall market is HEALTHY: the S&P 500 (SPY ${spy.Close:.2f}) is above its "
                f"200-day average (${spy.sma_slow:.2f}). New buys are allowed.")
    else:
        text = (f"Overall market is WEAK: the S&P 500 (SPY ${spy.Close:.2f}) is below its "
                f"200-day average (${spy.sma_slow:.2f}). The bot will NOT buy anything new "
                f"today. Most big crashes happen while the market is in this state.")
    return ok, text


def is_buy_candidate(r):
    return (r.Close > r.sma_fast > r.sma_slow and r.mom > 0 and r.rsi < C.RSI_MAX)


def plan(rows, spy, holdings, equity, cash, park_qty=0):
    """Decide what to do today.

    rows:     {symbol: latest row of indicators}
    holdings: {symbol: {"qty", "high", "entry"}}  (high = best close since we bought)
    park_qty: shares of the S&P 500 fund currently holding our spare cash
    Returns (market_text, actions, holds). Each action is a dict with side,
    symbol, qty, price and a plain-English "why" list. Order: sells, stock buys, park buy.
    """
    market_ok, market_text = market_check(spy)
    actions, holds = [], []

    # 1) Should we sell anything we own?
    for sym, h in holdings.items():
        r = rows.get(sym)
        if r is None:
            continue
        k = h.get("stop_atr", C.STOP_ATR)
        stop = h["high"] - k * r.atr
        why = []
        if r.Close < stop:
            why.append(f"Safety net hit: the price (${r.Close:.2f}) dropped below ${stop:.2f}. "
                       f"That's {k:g}x its normal daily swing below its best price since "
                       f"we bought (${h['high']:.2f}). Selling now stops a small loss from "
                       f"becoming a big one (or locks in the gain we already have).")
        elif r.Close < r.sma_fast:
            why.append(f"The uptrend broke: the price (${r.Close:.2f}) fell below its 50-day "
                       f"average (${r.sma_fast:.2f}). We bought it because it was rising, "
                       f"and that's no longer true.")
        if why:
            if h.get("entry"):
                pl = (r.Close - h["entry"]) * h["qty"]
                why.append(f"Result: bought at ${h['entry']:.2f}, now ${r.Close:.2f}, so this trade "
                           f"is {'UP' if pl >= 0 else 'DOWN'} about ${abs(pl):,.0f} "
                           f"({(r.Close / h['entry'] - 1):+.1%}).")
            actions.append(dict(side="SELL", symbol=sym, qty=h["qty"], price=r.Close, why=why))
        else:
            holds.append(f"KEEP {sym} ({name(sym)}): still in an uptrend. Safety net is now "
                         f"${stop:.2f} (price ${r.Close:.2f}).")

    # 2) Should we buy anything new? Parked money counts as spendable (we can sell some of it).
    selling = {a["symbol"] for a in actions}
    slots = C.MAX_POSITIONS - (len(holdings) - len(selling))
    parked = park_qty * spy.Close if C.PARK_IN_MARKET else 0
    spend = cash + parked - C.CASH_CUSHION * equity
    cands = [(s, r) for s, r in rows.items() if s not in holdings and is_buy_candidate(r)]
    cands.sort(key=lambda x: x[1].mom, reverse=True)
    if not market_ok:
        cands = []
    for rank, (sym, r) in enumerate(cands, 1):
        if slots <= 0:
            break
        stop_dist = C.STOP_ATR * r.atr
        qty = min(equity * C.RISK_PER_TRADE / stop_dist,
                  equity * C.MAX_POSITION_PCT / r.Close,
                  spend / r.Close)
        qty = math.floor(qty)
        if qty < 1:
            continue
        spend -= qty * r.Close
        slots -= 1
        actions.append(dict(side="BUY", symbol=sym, qty=qty, price=r.Close, rank=rank,
                            stop_atr=C.STOP_ATR, atr=r.atr, mom=r.mom, rsi=r.rsi,
                            sma_fast=r.sma_fast, sma_slow=r.sma_slow, equity=equity))

    # 3) Spare cash: keep it in the S&P 500 fund while the market is healthy
    if C.PARK_IN_MARKET:
        actions = _park(actions, spy, park_qty, equity, cash, spend, market_ok)
    return market_text, actions, holds


def _park(actions, spy, park_qty, equity, cash, spend, market_ok):
    px = spy.Close
    target = math.floor(max(spend, 0) / px) if market_ok else 0
    diff = target - park_qty
    buys_cost = sum(a["qty"] * a["price"] for a in actions if a["side"] == "BUY")
    must_fund = buys_cost > cash - C.CASH_CUSHION * equity  # stock buys need some parked money
    big = abs(diff) * px >= C.PARK_MIN_MOVE * equity
    common = dict(symbol=C.MARKET, price=px, kind="park")
    if diff < 0 and (not market_ok or must_fund or big):
        if not market_ok:
            why = [f"The overall market turned weak (S&P 500 below its 200-day average). The bot keeps "
                   f"spare money in plain cash during these times, because most big crashes happen then."]
        elif must_fund:
            why = [f"Frees up ${-diff * px:,.0f} to pay for today's stock buys. Spare cash waits in the "
                   f"S&P 500 fund until a stock pick needs it."]
        else:
            why = [f"Moves ${-diff * px:,.0f} of parked money back to plain cash to keep the account balanced."]
        actions.insert(0, dict(side="SELL", qty=-diff, why=why, **common))
    elif diff > 0 and big:
        why = [f"Spare cash: after today's other trades about ${diff * px:,.0f} would sit idle and earn nothing. "
               f"Putting it in the S&P 500 fund (SPY: 500 of the biggest US companies) lets it grow with the market.",
               f"The overall market is healthy, which is when the bot keeps money in it. It pulls money back out "
               f"to pay for new stock picks, or moves everything to cash if the market turns weak.",
               f"In 10 years of testing, parking spare cash like this added about 3-5% a year, with bigger swings."]
        actions.append(dict(side="BUY", qty=diff, why=why, **common))
    return actions


def buy_reasons(a):
    stop_dist = a["stop_atr"] * a["atr"]
    cost, eq = a["qty"] * a["price"], a["equity"]
    return [
        f"It's in an uptrend: price ${a['price']:.2f} is above its 50-day average "
        f"(${a['sma_fast']:.2f}), which is above its 200-day average (${a['sma_slow']:.2f}). "
        f"So it has been rising both recently and over the long run.",
        f"Strong momentum: it rose {a['mom']:.1%} over the last 3 months, #{a['rank']} best on the "
        f"watchlist. Stocks that have been rising tend to keep rising for a while.",
        f"Not overheated: its RSI is {a['rsi']:.0f}/100. Above {C.RSI_MAX} would mean it shot up "
        f"too fast and is more likely to fall back.",
        f"Size: ${cost:,.0f} = {cost / eq:.1%} of the account. Safety net at "
        f"${a['price'] - stop_dist:.2f} ({a['stop_atr']:g}x its normal daily swing of ${a['atr']:.2f} "
        f"below the price). If it's hit we lose about ${a['qty'] * stop_dist:,.0f} "
        f"(~{a['qty'] * stop_dist / eq:.1%} of the account), and the net moves up as the price rises.",
    ]


def describe(a):
    why = a["why"] if "why" in a else buy_reasons(a)
    head = f"{a['side']} {a['qty']} shares of {a['symbol']} ({name(a['symbol'])})"
    if a.get("price"):
        head += f" at about ${a['price']:.2f} each (~${a['qty'] * a['price']:,.0f})"
    return head + "\n" + "\n".join(f"  - {w}" for w in why)


def exposure_target(spy_close, avg, vol):
    """How many 'times the S&P 500' to hold: 0 when weak, else up to MAX_LEVERAGE in 0.25 steps."""
    if spy_close <= avg:
        return 0.0
    if not C.ADAPTIVE:
        return C.MAX_LEVERAGE
    return max(0.25, min(C.MAX_LEVERAGE, round(C.TARGET_VOL / max(vol, 1e-6) * 4) / 4))


def mix(L):
    """Exposure L built from the 2x fund and the plain S&P 500 fund: returns (2x share, 1x share)."""
    f = max(L - 1, 0.0)
    return f, min(L, 1.0) - f


def plan_trend(spy_close, avg, vol, prices, qtys, others, equity, cash):
    """2x trend strategy (optionally adaptive). prices/qtys: {symbol: ...} for the 2x fund and SPY.
    others = {symbol: qty} of anything else we own (sold when switching strategies).
    Returns (market_text, actions, holds) like plan()."""
    fund, mkt, days = C.TREND_FUND, C.MARKET, C.TREND_DAYS
    L = exposure_target(spy_close, avg, vol)
    f, g = mix(L)
    cur_L = (2 * qtys.get(fund, 0) * prices[fund] + qtys.get(mkt, 0) * prices[mkt]) / equity
    mood = "calm" if vol < 0.13 else "normal" if vol < 0.22 else "jumpy" if vol < 0.35 else "very jumpy"
    if L > 0:
        market_text = (f"Overall market is HEALTHY: the S&P 500 (${spy_close:.2f}) is above its {days}-day "
                       f"average (${avg:.2f}). Market mood: {mood} (it's been swinging about {vol:.0%} a year "
                       f"lately). Today's setting: {L:g}x the S&P 500"
                       + (f" = {f:.0%} in {fund} (2x fund) + {g:.0%} in SPY." if f and g else
                          f" = {f:.0%} in {fund} (2x fund)." if f else f" = {g:.0%} in SPY."))
    else:
        market_text = (f"Overall market is WEAK: the S&P 500 (${spy_close:.2f}) is below its {days}-day "
                       f"average (${avg:.2f}). The bot stays in cash until it recovers.")
    actions, holds = [], []
    for sym, qty in others.items():
        actions.append(dict(side="SELL", symbol=sym, qty=qty, price=None, kind="switch", why=[
            f"The bot now uses the 2x trend strategy, so it no longer holds {name(sym)}. Selling it "
            f"puts the money back into the plan (you can switch strategies in the What-if lab)."]))
    if abs(L - cur_L) < 0.25 - 1e-9 and not (L == 0 and cur_L > 0):
        if cur_L > 0:
            holds.append(f"KEEP the current mix (about {cur_L:.2f}x the S&P 500): close enough to today's target of {L:g}x.")
        return market_text, actions, holds

    investable = equity * (1 - C.CASH_CUSHION)
    target = {fund: math.floor(investable * f / prices[fund]), mkt: math.floor(investable * g / prices[mkt])}
    if L == 0:
        why = [f"The market turned weak (S&P 500 below its {days}-day average). The bot moves everything to "
               f"cash until it recovers. This rule is what dodged most of the 2008 crash in testing.",
               "Heads-up: sometimes the market dips below the average and quickly recovers, and the bot buys "
               "back a little higher. That small cost is the price of avoiding the big crashes."]
    else:
        direction = "more" if L > cur_L else "less"
        now = f"now about {cur_L:.2f}x" if cur_L else "you don't own any of it yet"
        why = [f"Target: {L:g}x the S&P 500 ({now}). The market is healthy and its mood is {mood}, "
               f"so the bot holds {direction} of the 2x fund than before.",
               f"How it adapts: it aims for a steady level of ups and downs (about {C.TARGET_VOL:.0%} a year). When the "
               f"market is calm it leans in, up to {C.MAX_LEVERAGE:g}x. When it gets jumpy it pulls back, because big "
               f"drops usually start in jumpy markets.",
               f"{fund} moves about 2x the S&P 500 each day; SPY moves 1x. Mixing them sets the overall level."]
    sells, buys = [], []
    for sym in (fund, mkt):
        diff = target[sym] - qtys.get(sym, 0)
        if diff < 0:
            sells.append(dict(side="SELL", symbol=sym, qty=-diff, price=prices[sym], kind="trend", why=why))
        elif diff > 0:
            buys.append(dict(side="BUY", symbol=sym, qty=diff, price=prices[sym], kind="trend", why=why))
    return market_text, actions + sells + buys, holds


# ---------- Half and half: 2x trend + all-weather ----------
def allweather_weights(closes, j):
    """All-weather half, decided with prices up to day position j (a month's last day).
    Holds every asset that is above its 200-day average, more of the calm ones and less of the jumpy ones,
    scaled so the mix swings about AW_TARGET_VOL a year. Whatever isn't needed sits in T-bills (AW_CASH).
    Never more than 100%: no borrowing."""
    r = closes.pct_change()
    up = [a for a in C.AW_ASSETS if closes[a].iloc[j] > closes[a].iloc[j - 200:j].mean()]
    if not up:
        return {C.AW_CASH: 1.0}, up
    raw = 1 / (r[up].iloc[j - 59:j + 1].std() * 252 ** 0.5)
    raw /= raw.sum()
    port_vol = float(np.sqrt(raw @ (r[up].iloc[j - 60:j].cov() * 252) @ raw))
    k = min(1.0, C.AW_TARGET_VOL / max(port_vol, 1e-6))
    w = {a: float(raw[a] * k) for a in up}
    w[C.AW_CASH] = 1 - k
    return w, up


def blend_targets(closes, i):
    """Target share of the account for each fund, using prices up to day position i (yesterday's close).
    closes: daily closes with columns for SPY, the 2x fund, the all-weather funds and T-bills."""
    spy = closes[C.MARKET]
    avg = float(spy.iloc[i - C.TREND_DAYS + 1:i + 1].mean())
    vol = float(spy.pct_change().iloc[i - C.VOL_DAYS + 1:i + 1].std() * 252 ** 0.5)
    L = exposure_target(float(spy.iloc[i]), avg, vol)
    f, g = mix(L)
    s = C.BLEND_TREND_SHARE
    w = {C.TREND_FUND: s * f, C.MARKET: s * g, C.AW_CASH: s * (1 - f - g)}
    aw, up = allweather_weights(closes, _month_end(closes.index, i))
    for a, x in aw.items():
        w[a] = w.get(a, 0.0) + (1 - s) * x
    return w, dict(L=L, avg=avg, vol=vol, spy=float(spy.iloc[i]), up=up, aw=aw)


def _month_end(index, i):
    """Position of the last trading day of a month, at or before position i.
    Day i counts only if the next trading day is in a new month (or, live, if i is the last day of its month)."""
    for j in range(i, 0, -1):
        if j + 1 < len(index):
            if index[j].month != index[j + 1].month:
                return j
        elif (index[j] + pd.offsets.BDay(1)).month != index[j].month:
            return j
    return i


def plan_blend(w, info, prices, qtys, others, equity):
    """Turn target shares into trades. Small differences (under REBALANCE_BAND of the account) are left alone."""
    s = C.BLEND_TREND_SHARE
    L, mood_vol = info["L"], info["vol"]
    mood = "calm" if mood_vol < 0.13 else "normal" if mood_vol < 0.22 else "jumpy" if mood_vol < 0.35 else "very jumpy"
    if L > 0:
        growth = (f"**Growth half ({s:.0%}):** the S&P 500 (${info['spy']:.2f}) is HEALTHY, above its "
                  f"{C.TREND_DAYS}-day average (${info['avg']:.2f}). Market mood: {mood}, so it holds {L:g}x the S&P 500.")
    else:
        growth = (f"**Growth half ({s:.0%}):** the S&P 500 (${info['spy']:.2f}) is WEAK, below its "
                  f"{C.TREND_DAYS}-day average (${info['avg']:.2f}), so this half waits in T-bills.")
    rising = ", ".join(name(a) for a in info["up"]) or "nothing"
    falling = ", ".join(name(a) for a in C.AW_ASSETS if a not in info["up"]) or "nothing"
    market_text = (growth + f"\n\n**All-weather half ({1 - s:.0%}):** rising now: {rising}. Skipped (falling): "
                   f"{falling}. Set at each month's end.")
    if C.PICKS:
        picks = ", ".join(f"{name(p)} ({p}) {x:.0%}" for p, x in C.PICKS.items())
        market_text += (f"\n\n**Your long-term picks:** {picks} of the account. The bot buys more after dips "
                        f"but never sells them; the two halves share the rest.")
    actions, holds = [], []
    for sym, qty in others.items():
        actions.append(dict(side="SELL", symbol=sym, qty=qty, price=None, kind="switch", why=[
            f"The bot now runs the half-and-half strategy, which doesn't hold {name(sym)}."]))
    investable = equity * (1 - C.CASH_CUSHION)
    sells, buys = [], []
    for sym in sorted(set(w) | {k for k, v in qtys.items() if v}):
        target = math.floor(investable * w.get(sym, 0.0) / prices[sym])
        have = qtys.get(sym, 0)
        diff = target - have
        if sym in C.PICKS and diff < 0:  # picks are only ever topped up, never trimmed
            holds.append(f"KEEP {name(sym)} ({sym}): {have * prices[sym] / equity:.0%} of the account. "
                         f"Your long-term pick: the bot never sells it.")
            continue
        off = abs(diff) * prices[sym] / equity
        if diff == 0 or (off < C.REBALANCE_BAND and target and have):
            if have:
                holds.append(f"KEEP {name(sym)} ({sym}): {have * prices[sym] / equity:.0%} of the account, "
                             f"target {w.get(sym, 0):.0%}.")
            continue
        why = _blend_why(sym, w.get(sym, 0.0), have * prices[sym] / equity, info)
        a = dict(side="SELL" if diff < 0 else "BUY", symbol=sym, qty=abs(diff), price=prices[sym],
                 kind="blend", why=why)
        (sells if diff < 0 else buys).append(a)
    return market_text, actions + sells + buys, holds


def _blend_why(sym, target, now, info):
    s = C.BLEND_TREND_SHARE
    head = f"Target {target:.0%} of the account (now {now:.0%})."
    if sym == C.TREND_FUND:
        return [head, f"Growth half: the market is {'healthy' if info['L'] else 'weak'}, so it holds "
                      f"{info['L']:g}x the S&P 500. {sym} moves about 2x the S&P 500 each day."]
    if sym in C.PICKS:
        return [head, f"{name(sym)} is your long-term pick. The bot keeps about {target:.0%} of the account in it, "
                      f"buys more when a dip pulls it more than 2% of the account below that, and never sells it."]
    if sym == C.AW_CASH:
        return [head, "T-bills are basically cash that earns interest. They hold the money that isn't needed "
                      "elsewhere: the growth half when the market is weak, and the all-weather half's spare part."]
    parts = []
    if sym == C.MARKET and mix(info["L"])[1] > 0:
        parts.append("the growth half holds some plain S&P 500 when the market is a bit jumpy")
    if sym in info["up"]:
        parts.append(f"{name(sym)} is rising (above its 200-day average), so the all-weather half holds it. "
                     f"Calmer assets get a bigger share")
    elif sym in C.AW_ASSETS:
        parts.append(f"{name(sym)} is no longer rising (below its 200-day average), so the all-weather half lets it go")
    reason = "; ".join(parts)
    return [head, (reason[0].upper() + reason[1:] + ".") if parts else "Rebalancing to the plan.",
            f"Why all-weather: stocks, bonds, gold, commodities and the dollar take turns rising. In testing "
            f"(2008-2026) this half made money in 16 of 19 years, including 2008."]

"""The bot's core steps, shared by the terminal (bot.py) and the dashboard (app.py).

make_plan()  -> looks at the market and your account, decides today's trades
execute()    -> places ONE trade you approved
Pending trades are saved in pending.json so the dashboard can show them.
"""
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import config as C
from broker import get_broker
from strategy import describe, load_history, plan, plan_trend

HERE = Path(__file__).parent
STATE = HERE / "state.json"      # best price + safety-net setting for each stock we own
JOURNAL = HERE / "journal.md"    # running diary of every decision
PENDING = HERE / "pending.json"  # today's trades waiting for your answer


def load_env():
    env = {}
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def notify(msg):
    subprocess.run(["osascript", "-e", "on run argv", "-e",
                    'display notification (item 1 of argv) with title "Trading Bot (paper)"',
                    "-e", "end run", msg], capture_output=True)


def journal(lines):
    with JOURNAL.open("a") as f:
        f.write("\n".join(lines) + "\n")


def load_state():
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    return {s: (v if isinstance(v, dict) else {"high": v}) for s, v in state.items()}


def save_state(state):
    STATE.write_text(json.dumps(state, indent=1))


def latest_rows(data):
    """Last *finished* day for each symbol (drop today's half-finished bar)."""
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    rows = {}
    for s, d in data.items():
        d = d[d.index < today].dropna()
        if len(d):
            rows[s] = d.iloc[-1]
    return rows


def _plain(a):
    """Make an action JSON-friendly (numpy numbers -> normal numbers)."""
    return {k: (float(v) if hasattr(v, "dtype") or isinstance(v, float) else v) for k, v in a.items()}


def make_plan(env, dry=False, with_commentary=True):
    """Returns a plan dict, or None if the market is closed."""
    broker = get_broker(env, dry)
    if not broker.market_is_open():
        return None
    equity, cash = broker.account()
    positions = broker.positions()
    if C.STRATEGY == "daytrade":
        market_text, actions, holds = _daytrade_plan(positions)
        ignored = []
    elif C.STRATEGY == "trend2x":
        market_text, actions, holds = _trend_plan(positions, equity, cash, dry)
        ignored = []
    elif C.STRATEGY == "blend":
        market_text, actions, holds = _blend_plan(positions, equity, dry)
        ignored = []
    else:
        market_text, actions, holds, ignored = _stock_plan(positions, equity, cash, dry)
    actions = [_plain(a) | {"status": "waiting"} for a in actions]

    key = env.get("ANTHROPIC_API_KEY")
    briefing = None
    if key and with_commentary:
        from commentary import add_commentary, market_briefing
        briefing = market_briefing(market_text, [describe(a) for a in actions], key)
        for a, n in zip(actions, add_commentary([describe(a) for a in actions], key)):
            a["note"] = n

    p = dict(date=str(date.today()), made=f"{datetime.now():%H:%M}", dry=dry, broker=broker.name,
             equity=equity, cash=cash, market_text=market_text, holds=holds,
             ignored=ignored, actions=actions, briefing=briefing)
    log = [f"\n## {datetime.now():%Y-%m-%d %H:%M} {'(dry run)' if dry else ''}\n",
           f"Broker: {broker.name}. Account value ${equity:,.0f}, cash ${cash:,.0f}\n", market_text + "\n"]
    if briefing:
        log.append(f"AI market check: {briefing}\n")
    log += [f"- {h}" for h in holds] + [f"- Ignoring {s}: not on the watchlist." for s in ignored]
    if not actions:
        log.append("No trades today. Nothing met the rules, which is normal on most days.")
    journal(log)
    return p


def _daytrade_plan(positions):
    others = {s: p for s, p in positions.items() if s != C.DT_SYMBOL}
    text = (f"Day-trading mode: the bot trades {C.DT_SYMBOL}'s opening range breakout during the day and "
            f"ends every day in cash. Arm it in the Day trading tab.")
    actions = [dict(side="SELL", symbol=s, qty=p["qty"], price=p.get("price"), kind="switch", why=[
        f"Day trading needs cash. Selling {s} frees about ${p['qty'] * p.get('price', p['entry']):,.0f} for it.",
        f"This locks in the current result on {s}: "
        f"{'+' if p.get('price', p['entry']) >= p['entry'] else '-'}${abs((p.get('price', p['entry']) - p['entry']) * p['qty']):,.0f}.",
        "Skip this if you'd rather keep the long-term position (day trading then has no cash to use)."])
        for s, p in others.items()]
    return text, actions, []


def _trend_plan(positions, equity, cash, dry):
    data = load_history([C.MARKET, C.TREND_FUND], years=2)
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    spy = data[C.MARKET].Close
    spy = spy[spy.index < today]  # finished days only
    vol = float(spy.pct_change().iloc[-C.VOL_DAYS:].std() * 252 ** 0.5)
    rows = latest_rows(data)
    prices = {s: float(rows[s].Close) for s in (C.TREND_FUND, C.MARKET)}
    qtys = {s: positions.get(s, {}).get("qty", 0) for s in prices}
    others = {s: p["qty"] for s, p in positions.items() if s not in prices and s != C.DT_SYMBOL}
    if not dry and others:
        state = load_state()
        for s in others:
            state.pop(s, None)
        save_state(state)
    return plan_trend(float(spy.iloc[-1]), float(spy.iloc[-C.TREND_DAYS:].mean()), vol,
                      prices, qtys, others, equity, cash)


def blend_universe():
    return list(dict.fromkeys([C.MARKET, C.TREND_FUND, C.AW_CASH] + list(C.AW_ASSETS)))


def blend_closes(years=2):
    import yfinance as yf
    syms = blend_universe()
    px = yf.download(syms, period=f"{years}y", auto_adjust=True, progress=False)
    return px["Close"][syms].dropna(), px["Open"][syms].dropna()


def _blend_plan(positions, equity, dry):
    from strategy import blend_targets, plan_blend
    closes, _ = blend_closes()
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    closes = closes[closes.index < today]  # finished days only
    w, info = blend_targets(closes, len(closes) - 1)
    prices = closes.iloc[-1].to_dict()
    qtys = {s: positions.get(s, {}).get("qty", 0) for s in prices}
    others = {s: p["qty"] for s, p in positions.items() if s not in prices and s != C.DT_SYMBOL}
    if not dry and others:
        state = load_state()
        for s in others:
            state.pop(s, None)
        save_state(state)
    return plan_blend(w, info, prices, qtys, others, equity)


def _stock_plan(positions, equity, cash, dry):
    data = load_history(list(C.WATCHLIST) + [C.MARKET], years=2)
    rows = latest_rows(data)
    spy = rows.pop(C.MARKET)

    state = load_state()
    holdings, ignored = {}, []
    park_qty = positions.get(C.MARKET, {}).get("qty", 0)
    for sym, p in positions.items():
        if sym == C.MARKET:
            continue  # the S&P 500 fund holds our parked spare cash
        if sym not in rows:
            ignored.append(sym)
            continue
        st = state.get(sym, {})
        high = max(st.get("high", rows[sym].Close), rows[sym].Close)
        holdings[sym] = {"qty": p["qty"], "high": high, "entry": p["entry"],
                         "stop_atr": st.get("stop_atr", C.STOP_ATR)}
    if not dry:  # remember today's best prices for the safety nets
        for sym, h in holdings.items():
            state[sym] = {"high": float(h["high"]), "stop_atr": h["stop_atr"]}
        save_state(state)

    market_text, actions, holds = plan(rows, spy, holdings, equity, cash, park_qty)
    return market_text, actions, holds, ignored


def save_pending(p):
    PENDING.write_text(json.dumps(p, indent=1))


def load_pending():
    """Today's plan, or None. Yesterday's unanswered trades are expired (prices moved)."""
    if not PENDING.exists():
        return None
    p = json.loads(PENDING.read_text())
    if p["date"] != str(date.today()):
        for a in p["actions"]:
            if a["status"] == "waiting":
                a["status"] = "expired"
    return p


def execute(env, a, approved):
    """Carry out your answer for one trade. Returns a short result message."""
    text = describe(a)
    if not approved:
        a["status"] = "skipped"
        journal([f"\n**SKIPPED (you said no)** {text}"])
        return f"Skipped {a['side']} {a['symbol']}."
    broker = get_broker(env)
    try:
        state = load_state()
        if a["side"] == "BUY":
            # never borrow money: re-check against the LIVE price and shrink the order to fit the cash
            _, cash = broker.account()
            live = broker.latest_price(a["symbol"])
            fit = int(cash * 0.995 // live)
            if fit < 1:
                raise RuntimeError(f"not enough cash (${cash:,.0f}) for this buy. If you skipped the "
                                   "sell that frees up cash, that's why.")
            if fit < a["qty"]:
                a["note_resized"] = f"Price moved to ${live:.2f}, so bought {fit} shares instead of {a['qty']} to stay within your cash."
                a["qty"] = fit
            a["price"] = live
        if a.get("kind") == "switch":
            if a["side"] == "SELL":
                broker.sell_all(a["symbol"])
            else:
                broker.buy(a["symbol"], a["qty"])
        elif a.get("kind") in ("trend", "blend"):
            if a["side"] == "SELL":  # only the shares the plan says (it used to sell everything here)
                held = broker.positions().get(a["symbol"], {}).get("qty", 0)
                if a["qty"] >= held:
                    broker.close_and_wait(a["symbol"])
                else:
                    broker.sell_and_wait(a["symbol"], a["qty"])
            else:
                broker.buy(a["symbol"], a["qty"])
        elif a.get("kind") == "park":
            if a["side"] == "SELL":
                broker.sell(a["symbol"], a["qty"])
            else:
                broker.buy(a["symbol"], a["qty"])
        elif a["side"] == "SELL":
            broker.sell_all(a["symbol"])
            state.pop(a["symbol"], None)
        else:
            broker.buy(a["symbol"], a["qty"])
            state[a["symbol"]] = {"high": a["price"], "stop_atr": a["stop_atr"]}
        save_state(state)
        a["status"] = "done"
        journal([f"\n**DONE{' (autopilot)' if C.AUTO_TRADE else ''}** {text}"])
        return f"Order sent: {a['side']} {a['qty']} {a['symbol']}." + (f" {a['note_resized']}" if a.get("note_resized") else "")
    except Exception as e:  # e.g. not enough cash, symbol not tradable
        a["status"] = "failed"
        a["error"] = str(e)
        journal([f"\n**FAILED** ({e}) {text}"])
        return f"Order FAILED for {a['symbol']}: {e}"

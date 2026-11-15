"""Day trading: opening range breakout on one fund, in and out the same day (PAPER account).

How it works, in plain English:
  1. Watch the first 15 minutes of trading. The highest and lowest prices in that window are
     "the opening range".
  2. If a 5-minute candle later CLOSES above the top of that range, buy with ~98% of the cash.
  3. Right away, place a stop order at the bottom of the range. The broker holds it, so it
     protects the trade even if this Mac goes to sleep.
  4. Once the trade is up as much as it risked (entry minus the stop), move the stop up to the buy price,
     so a winner can't turn into a loser. Tested better than profit targets or tighter trailing stops.
  5. If the stop isn't hit, sell everything at 3:50 PM New York time. Never hold overnight.
  At most one trade a day. If there's no breakout by 3:30 PM, no trade that day.

With the autopilot on (config.AUTO_TRADE), the market watch arms each trading day by itself.
With it paused, you arm each day in the dashboard. You can disarm or sell at any time.

Run by the dashboard as:  .venv/bin/python bot.py --daytrade   (keeps the Mac awake while it runs)
"""
import json
import math
import os
import subprocess
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

import config as C
import engine
from broker import get_broker

ET = ZoneInfo("America/New_York")
STATE = engine.HERE / "daytrade_state.json"
HISTORY = engine.HERE / "daytrade_history.json"
PIDFILE = engine.HERE / "daytrade.pid"

# Results of the 2016-2026 test (research/intraday.py: 5-minute bars, costs, next-bar fills), shown in the dashboard.
TESTED = dict(period="Jan 2016 - Nov 2026", per_year="2.9%", hold_per_year="18.1%", worst_drop="-15%",
              won="43%", recent="7.0%", days_traded="about 71%")


# ---------- state ----------
def load_state():
    today = str(date.today())
    s = json.loads(STATE.read_text()) if STATE.exists() else {}
    if s.get("date") != today:
        old = s
        s = {"date": today, "armed": False, "status": "idle", "symbol": C.DT_SYMBOL, "events": []}
        if old.get("status") == "in_trade":  # a trade from an earlier day was never closed
            s["leftover"] = {k: old.get(k) for k in ("date", "symbol", "qty", "entry", "stop_id")}
        save_state(s)
    return s


def save_state(s):
    STATE.write_text(json.dumps(s, indent=1))


def event(s, msg, notify=True):
    s["events"].append(f"{datetime.now(ET):%-I:%M %p} ET: {msg}")
    save_state(s)
    if notify:
        engine.notify(msg)


def history():
    return json.loads(HISTORY.read_text()) if HISTORY.exists() else []


def record(s, result, pnl=0.0):
    h = [d for d in history() if d["date"] != s["date"]]
    h.append({"date": s["date"], "symbol": s["symbol"], "result": result, "entry": s.get("entry"),
              "exit": s.get("exit"), "qty": s.get("qty"), "pnl": round(pnl, 2)})
    HISTORY.write_text(json.dumps(h, indent=1))


# ---------- process control (used by the dashboard) ----------
def running():
    try:
        pid = int(PIDFILE.read_text())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def arm(by="you"):
    s = load_state()
    if s["status"] in ("done", "stopped", "no_trade"):
        return "Today's trade is already finished."
    if s["status"] in ("idle", "disarmed"):
        s.update(armed=True, status="watching")
        event(s, ("Autopilot armed today's session." if by == "autopilot" else
                  "Armed for today: the bot will trade today's opening range breakout by itself."),
              notify=by == "autopilot")
    if not running():
        (engine.HERE / "logs").mkdir(exist_ok=True)
        log = open(engine.HERE / "logs" / "daytrade.log", "a")
        subprocess.Popen([str(engine.HERE / ".venv" / "bin" / "python"), str(engine.HERE / "bot.py"), "--daytrade"],
                         cwd=engine.HERE, stdout=log, stderr=log, start_new_session=True)
    return "Armed for today."


def disarm():
    s = load_state()
    if s["status"] == "in_trade":
        return "There's an open trade. Use 'Sell now' to close it first."
    s.update(armed=False, status="disarmed")
    event(s, "Disarmed: no day trade today.", notify=False)
    return "Disarmed."


def sell_now():
    s = load_state()
    if s["status"] != "in_trade":
        return "No open day trade."
    _exit(get_broker(engine.load_env()), s, "You sold it early")
    return "Sold."


# ---------- market data ----------
def bars_today(symbol):
    """Today's finished 5-minute candles (New York time)."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    env = engine.load_env()
    client = StockHistoricalDataClient(env["ALPACA_API_KEY"], env["ALPACA_SECRET_KEY"])
    now = datetime.now(ET)
    start = now.replace(hour=9, minute=30, second=0, microsecond=0)
    bars = client.get_stock_bars(StockBarsRequest(symbol_or_symbols=symbol, start=start, end=now,
                                                  timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                                                  feed=DataFeed.IEX)).df
    if bars.empty:
        return bars
    df = bars.droplevel(0)
    df.index = df.index.tz_convert(ET)
    return df[df.index + timedelta(minutes=5) <= now]  # finished candles only


# ---------- the trading steps ----------
def _exit(broker, s, why):
    if s.get("stop_id"):
        try:
            broker.cancel(s["stop_id"])
        except Exception:
            pass
    px = broker.close_and_wait(s["symbol"])
    pnl = (px - s["entry"]) * s["qty"]
    s.update(status="done", exit=px, pnl=pnl)
    event(s, f"{why}: sold {s['qty']} {s['symbol']} at ${px:.2f}. Today's result: "
             f"{'+' if pnl >= 0 else '-'}${abs(pnl):,.2f} ({px / s['entry'] - 1:+.2%}).")
    record(s, why, pnl)


def step(broker, s, now=None):
    """One check. Called about every 30 seconds while armed. (now can be given for testing.)"""
    now = now or datetime.now(ET)
    sym = s["symbol"]
    n = C.DT_RANGE_MIN // 5
    if s["status"] in ("done", "stopped", "disarmed", "no_trade") or not s.get("armed"):
        return
    if now.time() < (now.replace(hour=9, minute=30) + timedelta(minutes=C.DT_RANGE_MIN)).time():
        return  # still inside the opening range

    if "range" not in s:
        df = bars_today(sym)
        if len(df) < n:
            return
        hi, lo = float(df.high.iloc[:n].max()), float(df.low.iloc[:n].min())
        s["range"] = {"high": hi, "low": lo}
        event(s, f"Opening range for {sym}: ${lo:.2f} to ${hi:.2f}. The bot buys if a 5-minute candle "
                 f"closes above ${hi:.2f}, with a safety stop at ${lo:.2f}.")

    if s["status"] == "watching":
        if (now.hour, now.minute) >= C.DT_LAST_ENTRY:
            s["status"] = "no_trade"
            event(s, f"No breakout above ${s['range']['high']:.2f} by 3:30 PM, so no trade today. Cash stays safe.")
            record(s, "No breakout, no trade")
            return
        df = bars_today(sym)
        after = df.iloc[n:]
        if after.empty or float(after.close.iloc[-1]) <= s["range"]["high"]:
            return
        _, cash = broker.account()
        price = broker.latest_price(sym)
        qty = math.floor(cash * C.DT_CASH_USE / price)
        if qty < 1:
            s["status"] = "no_trade"
            event(s, "Breakout happened, but there isn't enough cash to buy. (Is the money still in another fund?)")
            record(s, "Not enough cash")
            return
        fill = broker.buy_and_wait(sym, qty)
        stop = s["range"]["low"]
        s.update(status="in_trade", entry=fill, qty=qty, stop=stop,
                 entered_at=now.replace(minute=now.minute // 5 * 5, second=0, microsecond=0).isoformat())
        s["stop_id"] = broker.stop_sell(sym, qty, stop)
        event(s, f"Breakout! Bought {qty} {sym} at ${fill:.2f} (${qty * fill:,.0f}). Safety stop at ${stop:.2f} "
                 f"(max loss about ${qty * (fill - stop):,.0f}). If it reaches ${2 * fill - stop:.2f}, the stop moves "
                 f"up to ${fill:.2f}. It sells by 3:50 PM either way.")
        return

    if s["status"] == "in_trade":
        status, px = broker.order_status(s["stop_id"])
        if status == "filled":
            pnl = (px - s["entry"]) * s["qty"]
            s.update(status="stopped", exit=px, pnl=pnl)
            what = "Breakeven stop hit" if s.get("breakeven") else "Safety stop hit"
            event(s, f"{what}: sold {s['qty']} {sym} at ${px:.2f}. Today's result: {'+' if pnl >= 0 else '-'}"
                     f"${abs(pnl):,.2f} ({px / s['entry'] - 1:+.2%}). No more trades today.")
            record(s, what, pnl)
        elif (now.hour, now.minute) >= C.DT_EXIT:
            _exit(broker, s, "End of day")
        elif not s.get("breakeven"):
            df = bars_today(sym)
            start = pd.Timestamp(s["entered_at"]) if s.get("entered_at") else df.index[min(n, len(df) - 1)]
            since = df[df.index >= start]
            if not since.empty and float(since.high.max()) >= s["entry"] + C.DT_BREAKEVEN_R * (s["entry"] - s["range"]["low"]):
                _to_breakeven(broker, s)


def _to_breakeven(broker, s):
    """The trade is up 1x its risk: swap the safety stop for one at the buy price."""
    try:
        broker.cancel(s["stop_id"])  # waits until the broker confirms
    except Exception:
        pass
    status, _ = broker.order_status(s["stop_id"])
    if status == "filled":
        return  # the old stop sold first; the next check records it
    if status != "canceled":
        return  # not confirmed yet; try again on the next check
    if broker.latest_price(s["symbol"]) <= s["entry"]:
        s["breakeven"] = True
        _exit(broker, s, "Back to the buy price after being up")
        return
    s["stop_id"] = broker.stop_sell(s["symbol"], s["qty"], s["entry"])
    s.update(stop=s["entry"], breakeven=True)
    event(s, f"Up {C.DT_BREAKEVEN_R:g}x the risk, so the safety stop moved up to the buy price (${s['entry']:.2f}). "
             f"Worst case for today's trade is now about break-even.")


def cleanup_leftover(broker):
    """If a day trade was left overnight (Mac off or process crashed at 3:50), sell it at the open."""
    s = load_state()
    prev = s.pop("leftover", None)
    if not prev:
        return
    if prev.get("stop_id"):
        try:
            broker.cancel(prev["stop_id"])
        except Exception:
            pass
    if prev["symbol"] in broker.positions():
        px = broker.close_and_wait(prev["symbol"])
        pnl = (px - prev["entry"]) * prev["qty"]
        engine.notify(f"Sold the leftover {prev['symbol']} day trade from {prev['date']} at ${px:.2f} "
                      f"(result {'+' if pnl >= 0 else '-'}${abs(pnl):,.0f}). It should have been sold at the close.")
        prev.update(status="done", exit=px)
        record(prev, "Sold next morning (missed the close)", pnl)
    save_state(s)


def run():
    """Main loop for an armed day: keeps the Mac awake, checks every 30 seconds, stops after the close."""
    PIDFILE.write_text(str(os.getpid()))
    awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    broker = get_broker(engine.load_env())
    try:
        while True:
            now = datetime.now(ET)
            if now.hour >= 16 or now.weekday() >= 5:
                break
            try:  # a network hiccup must never kill the loop while a trade is open
                if not broker.market_is_open():
                    time.sleep(60)
                    continue
                cleanup_leftover(broker)
                s = load_state()
                if not s.get("armed") or s["status"] in ("done", "stopped", "disarmed", "no_trade"):
                    break
                step(broker, s)
            except Exception as e:
                print(f"{now:%H:%M:%S} error: {e}", flush=True)
            time.sleep(30)
    finally:
        PIDFILE.unlink(missing_ok=True)
        awake.terminate()


def today_chart_data(symbol):
    try:
        return bars_today(symbol)
    except Exception:
        return pd.DataFrame()


def autopilot_check():
    """Called by the market watch every 15 minutes: arms the day automatically, or restarts the
    trading process if the Mac restarted. Never re-arms a day you disarmed yourself."""
    if not (C.AUTO_TRADE and (C.DAYTRADE_ON or C.STRATEGY == "daytrade")):
        return
    now = datetime.now(ET)
    if now.weekday() >= 5 or now.hour >= 16:
        return
    s = load_state()
    if s.get("leftover") and not running():
        cleanup_leftover(get_broker(engine.load_env()))
    if s["status"] == "in_trade" and not running():
        arm("autopilot")  # restart so the trade still gets closed at 3:50 (any time of day)
    elif (now.hour, now.minute) < C.DT_LAST_ENTRY and (
            s["status"] == "idle" or (s["status"] == "watching" and not running())):
        arm("autopilot")

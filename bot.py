"""Daily trading bot for a PAPER account (fake money).

  .venv/bin/python bot.py            decide today's trades, approve each one here in the terminal
  .venv/bin/python bot.py --dry-run  show what it would do, place nothing (no keys needed)
  .venv/bin/python bot.py --queue    (used by the daily schedule) decide today's trades and
                                     open the dashboard so you can approve them there
  .venv/bin/python bot.py --watch    (runs every 15 min) market watch: notifies you about big moves,
                                     the safety line, and gives a midday and closing update
  .venv/bin/python bot.py --daytrade (started by "Arm for today" in the dashboard) runs today's day trade

The dashboard (the Momentum app on your Desktop) does all of this with buttons.
"""
import socket
import subprocess
import sys
import time
from pathlib import Path

import engine
from strategy import describe

HERE = engine.HERE
PORT = 8501
URL = f"http://localhost:{PORT}"


def dashboard_running():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def start_dashboard():
    """Start the dashboard in the background (if it isn't already) and open it in the browser."""
    if not dashboard_running():
        (HERE / "logs").mkdir(exist_ok=True)
        log = open(HERE / "logs" / "dashboard.log", "a")
        subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(HERE / "app.py")],
                         cwd=HERE, stdout=log, stderr=log, start_new_session=True)
        for _ in range(30):
            if dashboard_running():
                break
            time.sleep(1)
    # open as its own app-style window in Chrome if you have it, otherwise your normal browser
    if Path("/Applications/Google Chrome.app").exists():
        subprocess.run(["open", "-na", "Google Chrome", "--args", f"--app={URL}", "--window-size=1300,900"])
    else:
        subprocess.run(["open", URL])


def ask_terminal(a, helper):
    """Approve, skip, or ask questions about one trade. Anything except a clear yes means Skip."""
    while True:
        print("\n" + describe(a) + (f"\n\nClaude's take: {a['note']}" if a.get("note") else ""))
        q = input("\n[y] approve  [n] skip  or type a question: ").strip()
        if q.lower() in ("y", "yes"):
            return True
        if q.lower() in ("", "n", "no"):
            return False
        if not helper:
            print("\nBot: Questions need an ANTHROPIC_API_KEY in .env.")
            continue
        print("  (thinking...)")
        print(f"\nBot: {helper.ask(q)}")


WATCH_STATE = HERE / "watch_state.json"


def watch():
    """Market watch: sends Mac notifications, never trades. Each alert fires at most once a day."""
    import json
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    import market
    from broker import get_broker
    env = engine.load_env()
    broker = get_broker(env)
    if not broker.market_is_open():
        return
    import daytrade
    try:
        daytrade.autopilot_check()
    except Exception as e:
        print(f"autopilot day-trade check failed: {e}", flush=True)
    today = str(date.today())
    sent = json.loads(WATCH_STATE.read_text()) if WATCH_STATE.exists() else {}
    sent = {today: sent.get(today, [])}
    live, avg, _ = market.safety_line()
    chg = market.snapshots([engine.C.MARKET])[engine.C.MARKET]["change"] or 0.0
    equity, _ = broker.account()
    pos = broker.positions()
    day_pl = sum(p["qty"] * p["price"] * p["change_today"] / (1 + p["change_today"]) for p in pos.values())
    acct = f"Your account: ${equity:,.0f} ({'+' if day_pl >= 0 else '-'}${abs(day_pl):,.0f} today)."
    gap = live / avg - 1
    alerts = []
    if chg <= -0.04:
        alerts.append(("drop4", f"Big drop: the S&P 500 is down {-chg:.1%} today. {acct}"))
    elif chg <= -0.02:
        alerts.append(("drop2", f"The S&P 500 is down {-chg:.1%} today. {acct}"))
    if chg >= 0.02:
        alerts.append(("jump2", f"The S&P 500 is up {chg:.1%} today. {acct}"))
    if engine.C.STRATEGY == "daytrade":
        gap = 1.0  # the 200-day safety line only matters for the long-term strategy
    if gap < 0:
        alerts.append(("below", f"The S&P 500 (${live:.2f}) is below the safety line (${avg:.2f}). If it closes "
                                f"there, tomorrow's check moves the 2x part to safety (T-bills)."))
    elif gap < 0.015:
        alerts.append(("near", f"Heads-up: the S&P 500 is only {gap:.1%} above the safety line (${avg:.2f})."))
    now_et = datetime.now(ZoneInfo("America/New_York"))
    if now_et.hour >= 12:
        alerts.append(("midday", f"Midday update: S&P 500 {chg:+.2%} today. {acct}"))
    if now_et.hour == 15 and now_et.minute >= 40:
        alerts.append(("close", f"Closing update: S&P 500 {chg:+.2%} today. {acct}"))
    for key, msg in alerts:
        if key not in sent[today]:
            engine.notify(msg)
            sent[today].append(key)
    WATCH_STATE.write_text(json.dumps(sent))


def main():
    if "--watch" in sys.argv:
        watch()
        return
    if "--daytrade" in sys.argv:
        import daytrade
        daytrade.run()
        return
    if "--open" in sys.argv:  # used by the Momentum app on your Desktop
        start_dashboard()
        return
    dry = "--dry-run" in sys.argv
    queue = "--queue" in sys.argv
    env = engine.load_env()

    if not queue:
        print("Looking at the market...")
    p = engine.make_plan(env, dry)
    if p is None:
        print("Market is closed right now (weekend/holiday/after hours). Nothing to do.")
        return

    if queue and engine.C.AUTO_TRADE:  # autopilot: carry out the long-term plan without asking
        done = [engine.execute(env, a, True) for a in p["actions"]]  # sells come first
        engine.save_pending(p)
        engine.notify("Autopilot: " + (" ".join(done) if done else
                                       "long-term holdings unchanged (nothing met the rules)."))
        return

    if queue:  # scheduled run: hand the decisions to the dashboard
        engine.save_pending(p)
        n = len(p["actions"])
        if engine.C.STRATEGY == "daytrade" or engine.C.DAYTRADE_ON:
            engine.notify((f"{n} long-term trade(s) waiting for your OK. " if n else "")
                          + "Day trading: click 'Arm for today' in Momentum if you want it to trade today.")
            start_dashboard()
        elif n:
            engine.notify(f"{n} trade(s) waiting for your OK. Opening the dashboard...")
            start_dashboard()
        else:
            engine.notify("No trades today. Nothing met the rules.")
        return

    print(f"\nAccount value ${p['equity']:,.0f}, cash ${p['cash']:,.0f}\n{p['market_text']}")
    for h in p["holds"]:
        print(h)
    if not p["actions"]:
        print("No trades today. Nothing met the rules, which is normal on most days.")

    key = env.get("ANTHROPIC_API_KEY")
    context = f"Account value ${p['equity']:,.0f}, cash ${p['cash']:,.0f}. {p['market_text']}"
    for a in p["actions"]:  # sells come first in the list
        if dry:
            print("\n" + describe(a) + "\n  (dry run: nothing sent)")
            continue
        helper = None
        if key:
            from assistant import Assistant
            helper = Assistant(key, a, context)
        print(engine.execute(env, a, ask_terminal(a, helper)))
    if not dry:
        engine.save_pending(p)  # so the dashboard shows what you decided


if __name__ == "__main__":
    main()

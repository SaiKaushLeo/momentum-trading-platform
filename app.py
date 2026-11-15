"""The bot's dashboard. Opens in your web browser, runs only on this Mac.

Start it by double-clicking "Trading Bot.command" (or: .venv/bin/python -m streamlit run app.py)
"""
import importlib
import json

from datetime import datetime

import altair as alt
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import backtest
import config as C
import daytrade
import engine
import market
from broker import get_broker
from strategy import describe, load_history, name

st.set_page_config(page_title="Momentum", page_icon=str(engine.HERE / "assets" / "icon.png"), layout="wide")
env = engine.load_env()
KEY = env.get("ANTHROPIC_API_KEY")


def md(text):
    """Streamlit treats $...$ as math, so escape dollar signs."""
    return text.replace("$", "\\$")


@st.cache_resource
def get_live_broker():
    return get_broker(env)


@st.cache_data(ttl=15, show_spinner=False)
def account_snapshot():
    b = get_live_broker()
    equity, cash = b.account()
    return b.market_is_open(), equity, cash, b.positions()


@st.cache_data(ttl=3600, show_spinner=False)
def latest_prices(symbols):
    return engine.latest_rows(load_history(list(symbols), years=2))


@st.cache_data(ttl=15, show_spinner=False)
def live_quotes(symbols):
    return market.snapshots(symbols)


@st.cache_data(ttl=600, show_spinner=False)
def safety_line_parts():
    _, avg, vol = market.safety_line()
    return avg, vol


@st.cache_data(ttl=60, show_spinner=False)
def chart_data(symbol, timeframe):
    return market.history(symbol, timeframe)


def refresh():
    account_snapshot.clear()
    live_quotes.clear()
    st.rerun()


def dark_mode():
    theme = getattr(getattr(st, "context", None), "theme", None)
    return getattr(theme, "type", "light") == "dark"


# ---------- sidebar: account + live market watch (updates itself every 30 seconds) ----------
@st.fragment(run_every="30s")
def sidebar_live():
    try:
        is_open, equity, cash, positions = account_snapshot()
    except (SystemExit, Exception) as e:
        st.error(f"Couldn't reach the broker: {e}")
        return
    st.metric("Account value", f"${equity:,.0f}", f"{equity / 100_000 - 1:+.2%} since start")
    st.metric("Cash", f"${cash:,.0f}")
    st.write(("🟢 Market is **open**" if is_open else "🔴 Market is **closed**")
             + f" · {len(positions)} stock(s) owned")
    try:
        q = live_quotes((C.MARKET,))[C.MARKET]
        avg, vol = safety_line_parts()
        gap = q["price"] / avg - 1
        st.markdown(md(f"**S&P 500 (SPY):** ${q['price']:.2f} ({q['change']:+.2%} today)"))
        line = (f"**Safety line:** ${avg:.2f} (200-day average). The market is {gap:.1%} above it."
                if gap > 0 else f"**Safety line:** ${avg:.2f}. The market is {-gap:.1%} BELOW it: "
                                 "the bot will move to cash at its next check.")
        (st.caption if gap > 0.02 else st.warning)(md(line))
    except Exception:
        st.caption("Live market data unavailable right now.")
    st.caption(f"Live · updated {datetime.now():%-I:%M:%S %p}")


with st.sidebar:
    st.image(str(engine.HERE / "assets" / "icon.png"), width=72)
    st.title("Momentum")
    st.caption("Paper trading only: fake money, real prices.")
    sidebar_live()
    if C.AUTO_TRADE:
        st.success("🤖 **Autopilot ON**: the bot buys and sells by itself.")
        if st.button("⏸ Pause autopilot", width="stretch",
                     help="Stops all automatic trading. Open positions stay as they are."):
            saved = json.loads((engine.HERE / "settings.json").read_text()) if (engine.HERE / "settings.json").exists() else {}
            (engine.HERE / "settings.json").write_text(json.dumps(saved | {"AUTO_TRADE": False}, indent=1))
            importlib.reload(C)
            st.rerun()
    else:
        st.warning("⏸ **Autopilot paused**: trades wait for your approval.")
        if st.button("▶️ Turn autopilot on", width="stretch"):
            saved = json.loads((engine.HERE / "settings.json").read_text()) if (engine.HERE / "settings.json").exists() else {}
            (engine.HERE / "settings.json").write_text(json.dumps(saved | {"AUTO_TRADE": True}, indent=1))
            importlib.reload(C)
            st.rerun()
    if st.button("Check for trades now", type="primary", width="stretch",
                 help="The bot also checks by itself at 8:00 AM on weekdays."):
        with st.spinner("Looking at the market" + (" and asking Claude..." if KEY else "...")):
            p = engine.make_plan(env)
        if p is None:
            st.warning("The market is closed, so there's nothing to trade right now.")
        else:
            engine.save_pending(p)
            refresh()
    if st.button("Refresh numbers", width="stretch"):
        refresh()

try:
    _, equity, cash, positions = account_snapshot()
except (SystemExit, Exception):
    st.stop()

tab_today, tab_news, tab_port, tab_day, tab_ask, tab_lab, tab_hist, tab_all = st.tabs(
    ["Today's trades", "Market news", "My portfolio", "Day trading", "Ask the bot", "What-if lab", "History",
     "All stocks"])


# ---------- Market news ----------
@st.cache_data(ttl=300, show_spinner=False)
def headlines():
    import news_ai
    return news_ai.latest_headlines(env)


@st.fragment(run_every="300s")
def news_live():
    try:
        items = headlines()
    except Exception:
        st.caption("News unavailable right now.")
        return
    if not items:
        st.caption("No market news in the last 24 hours.")
    for a in items:
        t = a["time"].astimezone().strftime("%a %-I:%M %p")
        st.markdown(md(f"**[{a['headline']}]({a['url']})**  \n{t} · {a['source'].title()}"))
        if a["summary"]:
            st.caption(md(a["summary"][:220]))
    st.caption(f"Updates every 5 minutes · last {datetime.now():%-I:%M %p}")


with tab_news:
    st.caption(md("Headlines about the S&P 500, Nasdaq, Dow and small companies from Alpaca's free news feed, "
                  "last 24 hours. **For information only:** the bot's own news AI (news_ai.py) was tested on "
                  "83,000 articles from 2019-2026 and guessed the market's direction no better than chance, so "
                  "news never decides a trade."))
    news_live()


# ---------- Today's trades ----------
def trade_chat(p, a):
    key = f"chat_{p['date']}_{a['symbol']}"
    history = st.session_state.setdefault(key, [])
    for role, text in history:
        st.markdown(md(f"**{'You' if role == 'user' else 'Bot'}:** {text}"))
    if not KEY:
        st.caption("Claude features are off (no API key). Ask your questions in your Claude Code chat instead.")
        return
    with st.form(key + "_form", clear_on_submit=True, border=False):
        q = st.text_input("Your question", placeholder="e.g. Would a 2x safety net be better? / Buy 10 shares instead",
                          label_visibility="collapsed")
        if st.form_submit_button("Ask") and q.strip():
            from assistant import Assistant
            context = f"Account value ${p['equity']:,.0f}, cash ${p['cash']:,.0f}. {p['market_text']}"
            helpers = st.session_state.setdefault("helpers", {})
            helper = helpers.setdefault(key, Assistant(KEY, a, context))
            helper.trade = a  # the trade as currently saved
            with st.spinner("Thinking..."):
                reply = helper.ask(q.strip())
            history += [("user", q.strip()), ("bot", reply)]
            engine.save_pending(p)  # keep any change the bot made to the trade
            st.rerun()


STATUS = {"done": ("✅", "Order sent"), "skipped": ("⏭️", "You skipped this one"),
          "failed": ("⚠️", "Order failed"), "expired": ("⌛", "Expired: it wasn't answered the day it was suggested")}

with tab_today:
    p = engine.load_pending()
    if not p:
        st.info("No suggestions yet. The bot checks at 8:00 AM on weekdays, or click **Check for trades now**.")
    else:
        st.caption(f"Suggested on {p['date']} at {p['made']} · {p['broker']}")
        (st.success if "HEALTHY" in p["market_text"] else st.warning)(md(p["market_text"]))
        if p.get("briefing"):
            level = p["briefing"].splitlines()[0].lower()
            box = st.error if "high" in level else st.warning if "elevated" in level else st.info
            box(md(f"**🤖 AI market check:** {p['briefing']}"))
        if p["holds"]:
            with st.expander(f"Stocks we're keeping ({len(p['holds'])})"):
                for h in p["holds"]:
                    st.markdown(md(f"- {h}"))
        if not p["actions"]:
            st.write("**No trades today.** Nothing met the rules, which is normal on most days.")
        for i, a in enumerate(p["actions"]):
            with st.container(border=True):
                lines = describe(a).splitlines()
                st.subheader(md(lines[0]))
                st.markdown(md("\n".join(f"- {l.strip()[2:]}" for l in lines[1:])))
                if a.get("note"):
                    st.info(md(f"**Claude's take:** {a['note']}"))
                if p.get("dry"):
                    st.caption("Practice run: these can't be approved.")
                elif a["status"] == "waiting":
                    c1, c2, _ = st.columns([1, 1, 4])
                    for col, label, ok in ((c1, "✅ Approve", True), (c2, "Skip", False)):
                        if col.button(label, key=f"{label}{i}", type="primary" if ok else "secondary",
                                      width="stretch"):
                            msg = engine.execute(env, a, ok)
                            engine.save_pending(p)
                            st.toast(msg)
                            refresh()
                else:
                    icon, label = STATUS.get(a["status"], ("", a["status"]))
                    st.markdown(f"{icon} **{label}**" + (f": {md(a['error'])}" if a.get("error") else ""))
                if a["status"] == "waiting":
                    with st.expander("💬 Ask about this trade"):
                        trade_chat(p, a)


# ---------- My portfolio (live, updates every 30 seconds) ----------
@st.fragment(run_every="30s")
def portfolio_live():
    try:
        _, equity, cash, positions = account_snapshot()
    except Exception:
        st.warning("Can't reach the broker right now (internet hiccup?). Trying again in 30 seconds.")
        return
    if not positions:
        st.info("You don't own any stocks yet. Approved buys show up here.")
        return
    state = engine.load_state()
    rows = latest_prices(tuple(sorted(positions)))
    try:
        avg, _ = safety_line_parts()
    except Exception:
        avg = None
    table = []
    for sym, pos in sorted(positions.items()):
        price = pos.get("price") or pos["entry"]
        if sym in (C.MARKET, C.TREND_FUND):
            net = (f"Sells if the S&P 500 closes below ${avg:,.2f}" if avg else "Sells if the market turns weak")
        else:
            r, st_ = rows.get(sym), state.get(sym, {})
            k = st_.get("stop_atr", C.STOP_ATR)
            net = (f"Sells if it closes below ${max(st_.get('high', price), price) - k * float(r.atr):,.2f}"
                   if r is not None else "")
        table.append({"Stock": sym, "Name": name(sym), "Shares": pos["qty"], "Paid (avg)": pos["entry"],
                      "Price now": price, "Value": pos["qty"] * price,
                      "Gain/loss": (price - pos["entry"]) * pos["qty"], "Gain/loss %": price / pos["entry"] - 1,
                      "Today": pos.get("change_today"), "Safety net": net})
    money = st.column_config.NumberColumn(format="$%.2f")
    pct = st.column_config.NumberColumn(format="percent")
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch", column_config={
        "Paid (avg)": money, "Price now": money, "Value": money, "Gain/loss": money,
        "Gain/loss %": pct, "Today": pct,
        "Safety net": st.column_config.TextColumn(help="When the bot will suggest selling.")})
    total = sum(r["Gain/loss"] for r in table)
    st.markdown(md(f"**Total gain/loss right now:** {'+' if total >= 0 else '-'}${abs(total):,.2f} · cash ${cash:,.0f} · "
                   f"live prices, updated {datetime.now():%-I:%M:%S %p}"))


with tab_port:
    portfolio_live()


# ---------- Ask the bot ----------
with tab_ask:
    st.write("Ask anything about the bot, your trades, or what a word means.")
    if not KEY:
        st.info("Claude features are turned off (no API key). The bot trades and explains every decision "
                "without them. For questions, ask in your Claude Code chat. To turn them on later, add "
                "ANTHROPIC_API_KEY to .env.")
    else:
        if "general_chat" not in st.session_state:
            from assistant import Assistant
            recent = engine.JOURNAL.read_text()[-4000:] if engine.JOURNAL.exists() else "(no trades yet)"
            st.session_state.general_chat = (Assistant(KEY, context=(
                f"Account value ${equity:,.0f}, cash ${cash:,.0f}, owns: {', '.join(positions) or 'nothing'}.\n"
                f"Recent bot journal:\n{recent}")), [])
        helper, history = st.session_state.general_chat
        box = st.container(height=420)
        for role, text in history:
            box.chat_message(role).markdown(md(text))
        if q := st.chat_input("e.g. Why did the bot pick Microsoft? / What does ATR mean?"):
            history.append(("user", q))
            box.chat_message("user").markdown(md(q))
            with box.chat_message("assistant"), st.spinner("Thinking..."):
                reply = helper.ask(q)
            history.append(("assistant", reply))
            st.rerun()


# ---------- What-if lab ----------
def curve_chart(df):
    theme = getattr(getattr(st, "context", None), "theme", None)
    dark = getattr(theme, "type", "light") == "dark"
    colors = ["#3987e5", "#d95926"] if dark else ["#2a78d6", "#eb6834"]
    cols = list(df.columns)
    long = df.reset_index(names="Date").melt("Date", var_name="Series", value_name="Value")
    near = alt.selection_point(nearest=True, on="pointerover", fields=["Date"], empty=False)
    base = alt.Chart(long).encode(x=alt.X("Date:T", title=None))
    lines = base.mark_line(strokeWidth=2).encode(
        y=alt.Y("Value:Q", title="Account value", axis=alt.Axis(format="$,.0s", grid=True, gridOpacity=0.3)),
        color=alt.Color("Series:N", scale=alt.Scale(domain=cols, range=colors),
                        legend=alt.Legend(orient="top", title=None)))
    dots = lines.mark_point(size=64, filled=True).encode(
        opacity=alt.condition(near, alt.value(1), alt.value(0)))
    rule = alt.Chart(df.reset_index(names="Date")).mark_rule(color="gray").encode(
        x="Date:T", opacity=alt.condition(near, alt.value(0.5), alt.value(0)),
        tooltip=[alt.Tooltip("Date:T")] + [alt.Tooltip(field=c, type="quantitative", format="$,.0f")
                                           for c in cols]).add_params(near)
    return (lines + dots + rule).properties(height=340)


with tab_lab:
    st.write("Try different settings and see how the bot's rules would have done over the last ~20 years.")
    strat_names = {"blend": "Half and half (2x trend + all-weather: steadier in bad markets)",
                   "daytrade": "Day trading (opening range breakout, in and out each day)",
                   "trend2x": "2x trend (2x S&P 500 fund in healthy markets, cash when weak)",
                   "stocks": "Stock picking (up to 5 rising stocks)"}
    strat = st.radio("Strategy", list(strat_names), format_func=strat_names.get,
                     index=list(strat_names).index(C.STRATEGY), horizontal=True)
    t1, t2, t3 = st.columns([2, 1, 1])
    trend_days = t1.slider("2x trend: 'healthy' means the S&P 500 is above its average over this many days",
                           100, 300, int(C.TREND_DAYS), 25, disabled=strat not in ("trend2x", "blend"))
    adaptive = t2.toggle("Adapt to market mood", C.ADAPTIVE, disabled=strat not in ("trend2x", "blend"),
                         help="Hold less of the 2x fund when the market is jumpy, more when it's calm.")
    target_vol = t3.slider("Target yearly swing (%)", 15, 35, int(C.TARGET_VOL * 100), 5,
                           disabled=strat not in ("trend2x", "blend") or not adaptive) / 100
    st.caption("Stock-picking settings:")
    c1, c2, c3, c4 = st.columns(4)
    stop = c1.slider("Safety net distance (x daily swing)", 1.0, 6.0, float(C.STOP_ATR), 0.5,
                     help="Smaller = sells sooner on a drop. Bigger = gives the stock more room.")
    risk = c2.slider("Risk per trade (% of account)", 0.5, 3.0, C.RISK_PER_TRADE * 100, 0.25,
                     help="How much you'd lose if a safety net is hit. Bigger = bigger trades.") / 100
    maxp = c3.slider("Max stocks owned", 1, 10, C.MAX_POSITIONS)
    rsi = c4.slider("Skip if 'overheated' above (RSI)", 60, 90, int(C.RSI_MAX), 5)
    park_on = st.toggle("Keep spare cash in the S&P 500 fund while the market is healthy", C.PARK_IN_MARKET)
    settings = {"STOP_ATR": stop, "RISK_PER_TRADE": risk, "MAX_POSITIONS": maxp, "RSI_MAX": rsi,
                "PARK_IN_MARKET": park_on, "STRATEGY": strat, "TREND_DAYS": trend_days,
                "ADAPTIVE": adaptive, "TARGET_VOL": target_vol}

    if strat == "daytrade":
        t = daytrade.TESTED
        st.info(md(f"Day trading is tested on 5-minute prices, which this lab can't replay. Result of the "
                   f"{t['period']} test for {C.DT_SYMBOL}: about {t['per_year']} a year (just holding: "
                   f"{t['hold_per_year']}), worst drop {t['worst_drop']}, won {t['won']} of trades. "
                   f"See the Day trading tab."))
        if C.STRATEGY != "daytrade" and st.button("Switch the bot to day trading"):
            saved = json.loads((engine.HERE / "settings.json").read_text()) if (engine.HERE / "settings.json").exists() else {}
            (engine.HERE / "settings.json").write_text(json.dumps(saved | {"STRATEGY": "daytrade"}, indent=1))
            importlib.reload(C)
            st.rerun()
    elif st.button("Run test", type="primary"):
        with st.spinner("Replaying 20 years of prices..."):
            st.session_state.lab = (settings, backtest.run(quiet=True),
                                    backtest.run(quiet=True, **settings), backtest.last_curve)
    if "lab" in st.session_state:
        tested, now, new, curve = st.session_state.lab
        m = st.columns(4)
        pct = lambda s: float(s.strip("%"))
        m[0].metric("Growth per year", new["yearly_growth"],
                    f"{pct(new['yearly_growth']) - pct(now['yearly_growth']):+.1f} pts vs current settings")
        m[1].metric("Worst drop", new["worst_drop"],
                    f"{pct(new['worst_drop']) - pct(now['worst_drop']):+.1f} pts vs current", delta_color="normal")
        m[2].metric("Trades", new["trades"])
        m[3].metric("Winning trades", new["win_rate"])
        st.caption(f"Just holding the S&P 500: {new['spy_yearly_growth']} per year, worst drop {new['spy_worst_drop']}.")
        st.altair_chart(curve_chart(curve.rename(columns={"Bot": "Bot (these settings)"})),
                        width="stretch")
        with st.expander("Show as a table"):
            st.dataframe(curve.resample("ME").last().style.format("${:,.0f}"), width="stretch")
        st.warning("Past results don't guarantee the future. Picking whatever looked best in the past often "
                   "disappoints, so prefer settings that are good across many tests."
                   + (" Stock picking also looks better than reality: its watchlist is today's winners."
                      if tested["STRATEGY"] == "stocks" else ""))
        if st.button("Use these settings for the bot from now on"):
            saved = json.loads((engine.HERE / "settings.json").read_text()) if (engine.HERE / "settings.json").exists() else {}
            (engine.HERE / "settings.json").write_text(json.dumps(saved | tested, indent=1))  # keeps autopilot etc.
            importlib.reload(C)
            st.success("Saved. The bot will use these settings from its next check.")
    if (engine.HERE / "settings.json").exists() and st.button("Go back to the original settings"):
        (engine.HERE / "settings.json").unlink()
        importlib.reload(C)
        st.rerun()


# ---------- History ----------
with tab_hist:
    if not engine.JOURNAL.exists():
        st.info("Nothing yet. Every check and decision will be listed here.")
    else:
        entries = engine.JOURNAL.read_text().split("\n## ")
        for e in reversed(entries[-30:]):
            if e.strip():
                with st.container(border=True):
                    title, _, body = e.strip().partition("\n")
                    st.markdown(md(f"**{title.lstrip('# ')}**\n{body}"))


# ---------- All stocks: one sub-tab per exchange, live prices + candlestick charts ----------
US_LIST = [C.MARKET, C.TREND_FUND] + list(C.WATCHLIST)
US = dict(tz="America/New_York", hours=((9, 30), (16, 0)), cur="$")


@st.cache_data(ttl=60, show_spinner=False)
def delayed_quotes(symbols):
    return market.quotes_delayed(symbols)


@st.cache_data(ttl=60, show_spinner=False)
def chart_data_tz(symbol, timeframe, tz):
    return market.history(symbol, timeframe, tz)


def quote_table(symbols, names, quotes, cur, note):
    rows = [{"Stock": s, "Name": names.get(s, s), "Price": q["price"], "Today": q["change"],
             "Day low": q["low"], "Day high": q["high"]} for s in symbols if (q := quotes.get(s))]
    fmt = f"{cur.strip()} %.2f" if cur != "$" else "$%.2f"
    money = st.column_config.NumberColumn(format=fmt)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=300, column_config={
        "Price": money, "Day low": money, "Day high": money,
        "Today": st.column_config.NumberColumn(format="percent")})
    st.caption(f"{note} Updated {datetime.now():%-I:%M:%S %p}, refreshes every 30 seconds.")


def candle_chart(df, symbol, style, show_avgs, show_vol, cur="$", hours=((9, 30), (16, 0))):
    dark = dark_mode()
    up, down = ("#2ebd85", "#f6465d")
    avg_colors = ["#3987e5", "#d95926"] if dark else ["#2a78d6", "#eb6834"]
    rows = 2 if show_vol and "Volume" in df and df.Volume.sum() > 0 else 1
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                        row_heights=[0.75, 0.25] if rows == 2 else [1])
    if style == "Candlesticks":
        fig.add_trace(go.Candlestick(x=df.index, open=df.Open, high=df.High, low=df.Low, close=df.Close,
                                     name=symbol, increasing_line_color=up, decreasing_line_color=down,
                                     increasing_fillcolor=up, decreasing_fillcolor=down), row=1, col=1)
        line_colors = avg_colors
    else:
        fig.add_trace(go.Scatter(x=df.index, y=df.Close, name=symbol, mode="lines",
                                 line=dict(width=2, color=avg_colors[0])), row=1, col=1)
        line_colors = [avg_colors[1], "#8a8a8a"]
    if show_avgs:
        for col, color in zip(("50-day average", "200-day average"), line_colors):
            if col in df and df[col].notna().any():
                fig.add_trace(go.Scatter(x=df.index, y=df[col], name=col, mode="lines",
                                         line=dict(width=2, color=color)), row=1, col=1)
    if rows == 2:
        colors = [up if c >= o else down for o, c in zip(df.Open, df.Close)]
        fig.add_trace(go.Bar(x=df.index, y=df.Volume, name="Volume", marker_color=colors, opacity=0.5,
                             showlegend=False), row=2, col=1)
        fig.update_yaxes(title_text="Volume", row=2, col=1)
    intraday = (df.index[-1] - df.index[0]).days < 40
    (oh, om), (ch, cm) = hours
    breaks = [dict(bounds=["sat", "mon"])] + ([dict(bounds=[ch + cm / 60, oh + om / 60], pattern="hour")]
                                               if intraday else [])
    fig.update_xaxes(rangebreaks=breaks, rangeslider_visible=False, showspikes=True, spikemode="across",
                     spikethickness=1, spikedash="dot")
    fig.update_yaxes(tickprefix=cur, row=1, col=1, showspikes=True, spikethickness=1, spikedash="dot")
    fig.update_layout(height=560, margin=dict(l=10, r=10, t=30, b=10), hovermode="x unified",
                      legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0))
    return fig


def chart_section(key, choices, names, ex):
    c1, c2, c3 = st.columns([2, 2, 3])
    pick = c1.selectbox("Stock", choices, format_func=lambda s: f"{s} · {names.get(s, s)}", key=f"pick_{key}")
    other = c2.text_input("…or type any ticker", key=f"other_{key}",
                          placeholder={"US": "e.g. TSLA", "NSE": "e.g. WIPRO.NS", "BSE": "e.g. WIPRO.BO",
                                       "DFM": "e.g. ALANSARI.AE"}[key]).strip().upper()
    symbol = other or pick
    tf = c3.radio("Time range", list(market.TIMEFRAMES), index=4, horizontal=True, key=f"tf_{key}")
    o1, o2, o3 = st.columns(3)
    style = o1.radio("Chart type", ["Candlesticks", "Line"], horizontal=True, key=f"style_{key}")
    show_avgs = o2.toggle("50/200-day averages", True, key=f"avg_{key}")
    show_vol = o3.toggle("Volume", True, key=f"vol_{key}")
    with st.spinner(f"Loading {symbol}..."):
        df = chart_data_tz(symbol, tf, ex["tz"])
    if df.empty:
        st.warning(f"No price data found for '{symbol}'. Check the ticker.")
        return
    last, first = float(df.Close.iloc[-1]), float(df.Close.iloc[0])
    st.markdown(md(f"### {symbol} · {ex['cur']}{last:,.2f}  ({last / first - 1:+.2%} over {tf})"))
    st.plotly_chart(candle_chart(df, symbol, style, show_avgs, show_vol, ex["cur"], ex["hours"]),
                    width="stretch", theme="streamlit", key=f"chart_{key}")
    st.caption(f"Times are local exchange time ({ex['tz'].split('/')[-1].replace('_', ' ')}).")
    with st.expander("Show as a table"):
        st.dataframe(df[["Open", "High", "Low", "Close", "Volume"]].iloc[::-1], width="stretch")


def status_line(is_open, now, label, hours):
    (oh, om), (ch, cm) = hours
    st.write(f"{'🟢 **Open**' if is_open else '🔴 **Closed**'} · local time {now:%a %-I:%M %p} · "
             f"trading hours {oh}:{om:02d}–{ch}:{cm:02d} Mon–Fri")


@st.fragment(run_every="30s")
def us_table():
    try:
        q = live_quotes(tuple(US_LIST))
    except Exception as e:
        st.error(f"Live prices unavailable right now: {e}")
        return
    quote_table(US_LIST, {s: name(s) for s in US_LIST}, q, "$", "Live prices (IEX feed).")


def make_foreign_table(key):
    ex = market.EXCHANGES[key]
    syms = [ex["index"][0]] + list(ex["stocks"])
    names = {ex["index"][0]: ex["index"][1], **ex["stocks"]}

    @st.fragment(run_every="30s")
    def table():
        is_open, now = market.exchange_status(key)
        status_line(is_open, now, ex["label"], ex["hours"])
        quote_table(syms, names, delayed_quotes(tuple(syms)), ex["cur"],
                    "Prices from Yahoo Finance, delayed about 15 minutes.")
    return table, syms, names


with tab_all:
    sub = st.tabs(["🇺🇸 NYSE & Nasdaq (US)"] + [market.EXCHANGES[k]["label"] for k in market.EXCHANGES]
                  + ["🇦🇪 Abu Dhabi (ADX)"])
    with sub[0]:
        st.caption("This is the only market the bot trades in (through Alpaca). The other tabs are for watching.")
        from zoneinfo import ZoneInfo
        us_now = datetime.now(ZoneInfo(US["tz"]))
        us_open = account_snapshot()[0]
        status_line(us_open, us_now, "US", US["hours"])
        us_table()
        chart_section("US", US_LIST, {s: name(s) for s in US_LIST}, US)
        with st.expander("How to read a candlestick"):
            st.markdown(md("Each candle is one time period (5 minutes on 1D, one day on 1Y, one week on 5Y). "
                           "**Green** = price ended higher than it started; **red** = lower. The thick body spans "
                           "the start and end price; the thin wicks show the highest and lowest prices in between. "
                           "The **200-day average** line is what the bot uses to decide if the market is healthy."))
    for tab, key in zip(sub[1:], market.EXCHANGES):
        with tab:
            st.caption("View only: the bot can't trade here (Alpaca only supports US stocks).")
            table, syms, names = make_foreign_table(key)
            table()
            ex = market.EXCHANGES[key]
            choices = syms if ex.get("index_has_history", True) else list(ex["stocks"])  # no free index history
            chart_section(key, choices, names, ex)
    with sub[-1]:
        st.info("**No free live data for the Abu Dhabi Securities Exchange (ADX).** Yahoo Finance and Alpaca, "
                "the free sources this app uses, don't cover ADX stocks. You can follow it on ADX's own website, "
                "and if you later get a data provider that covers ADX, a tab like the Dubai one can be added.")
        st.link_button("Open adx.ae (official site)", "https://www.adx.ae")
        st.caption("Big ADX companies include First Abu Dhabi Bank (FAB), International Holding Company (IHC), "
                   "ADNOC Gas, Aldar Properties, e& (Etisalat), Abu Dhabi Commercial Bank (ADCB), and TAQA.")


# ---------- Day trading ----------
STATUS_TEXT = {"idle": "⚪ Not armed yet today", "disarmed": "⚪ Disarmed for today",
               "watching": "🟡 Armed: waiting for a breakout", "in_trade": "🟢 In a trade",
               "done": "✅ Done for today", "stopped": "🛑 Safety stop hit, done for today",
               "no_trade": "⚪ No breakout today, no trade"}


def day_chart(df, s):
    dark = dark_mode()
    up, down = ("#2ebd85", "#f6465d")
    line = "#c3c2b7" if dark else "#555555"
    fig = go.Figure(go.Candlestick(x=df.index.tz_localize(None), open=df.open, high=df.high, low=df.low,
                                   close=df.close, name=s["symbol"], increasing_line_color=up,
                                   decreasing_line_color=down, increasing_fillcolor=up, decreasing_fillcolor=down))
    if s.get("range"):
        fig.add_hline(y=s["range"]["high"], line=dict(color=line, dash="dot", width=1),
                      annotation_text="buy above this", annotation_position="top left")
        fig.add_hline(y=s["range"]["low"], line=dict(color=line, dash="dot", width=1),
                      annotation_text="safety stop", annotation_position="bottom left")
    if s.get("entry"):
        fig.add_hline(y=s["entry"], line=dict(color="#3987e5" if dark else "#2a78d6", width=2),
                      annotation_text=f"bought ${s['entry']:.2f}", annotation_position="top right")
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=20, b=10), xaxis_rangeslider_visible=False,
                      hovermode="x unified", showlegend=False)
    fig.update_yaxes(tickprefix="$")
    return fig


@st.fragment(run_every="30s")
def day_live():
    s = daytrade.load_state()
    sym = s["symbol"]
    st.subheader(STATUS_TEXT.get(s["status"], s["status"]))
    if s["status"] == "in_trade":
        try:
            px = live_quotes((sym,))[sym]["price"]
            pnl = (px - s["entry"]) * s["qty"]
            c1, c2, c3 = st.columns(3)
            c1.metric(f"{sym} now", f"${px:.2f}", f"{px / s['entry'] - 1:+.2%} since you bought")
            c2.metric("Trade result so far", f"{'+' if pnl >= 0 else '-'}${abs(pnl):,.2f}")
            c3.metric("Safety stop", f"${s['stop']:.2f}", f"max loss ~${s['qty'] * (s['entry'] - s['stop']):,.0f}",
                      delta_color="off")
        except Exception:
            st.caption("Live price unavailable right now.")
    elif s.get("pnl") is not None and s["status"] in ("done", "stopped"):
        st.metric("Today's result", f"{'+' if s['pnl'] >= 0 else '-'}${abs(s['pnl']):,.2f}")
    if s.get("armed") and s["status"] in ("watching", "in_trade") and not daytrade.running():
        st.warning("The bot is armed but its trading process isn't running (the Mac may have restarted). "
                   "Click 'Arm for today' again to restart it. Any open trade is still protected by the broker's stop.")
    df = daytrade.today_chart_data(sym)
    if not df.empty:
        st.plotly_chart(day_chart(df, s), width="stretch", theme="streamlit", key="day_chart")
    else:
        st.caption(f"Today's {sym} chart appears once the market opens (6:30 AM your time).")
    if s["events"]:
        st.markdown("**Today's log**")
        for e in reversed(s["events"]):
            st.markdown(md(f"- {e}"))


with tab_day:
    def save_daytrade_on(on):
        saved = json.loads((engine.HERE / "settings.json").read_text()) if (engine.HERE / "settings.json").exists() else {}
        (engine.HERE / "settings.json").write_text(json.dumps(saved | {"DAYTRADE_ON": on}, indent=1))
        importlib.reload(C)
        st.rerun()

    if C.STRATEGY != "daytrade" and not C.DAYTRADE_ON:
        t = daytrade.TESTED
        st.info(md(f"**Day trading is off.** In a 10-year test ({t['period']}) it made about {t['per_year']} a year, "
                   f"while just holding {C.DT_SYMBOL} made {t['hold_per_year']}. Betting against the market and "
                   f"news-based versions did worse. The autopilot won't day trade until you turn it back on."))
        if st.button("Turn day trading back on"):
            save_daytrade_on(True)
    elif C.STRATEGY != "daytrade" and st.button("Turn day trading off",
                                                help="Stops the autopilot from day trading. Any open trade keeps its stop."):
        save_daytrade_on(False)
    st.markdown(md(
        f"**How it works:** the bot watches the first {C.DT_RANGE_MIN} minutes of trading in **{C.DT_SYMBOL}** "
        f"(a fund of the 100 biggest Nasdaq companies). If {C.DT_SYMBOL} then breaks above that range, it buys with "
        f"about {C.DT_CASH_USE:.0%} of your cash and immediately sets a **safety stop** at the bottom of the range. "
        f"Alpaca holds that stop, so it works even if your Mac sleeps. Once the trade is up as much as it risked, the "
        f"stop moves up to the buy price, so a winner can't turn into a loser. If the stop isn't hit, it sells everything at "
        f"3:50 PM New York time (1:50 PM yours). One trade a day at most, and it never holds overnight."))
    try:
        positions = account_snapshot()[3]
    except Exception:
        positions = {}
    others = {k: v for k, v in positions.items() if k != C.DT_SYMBOL}
    if C.STRATEGY != "daytrade":
        try:
            _, _, cash_now, _ = account_snapshot()
            qq = live_quotes((C.DT_SYMBOL,))[C.DT_SYMBOL]["price"]
            n_sh = int(cash_now * C.DT_CASH_USE // qq)
            st.info(md(f"**Test mode:** day trading uses only your spare cash (${cash_now:,.0f}), which buys about "
                       f"**{n_sh} share(s)** of {C.DT_SYMBOL} at ${qq:,.2f}. Your long-term holdings keep following "
                       f"the long-term rules and are never touched by day trading."))
        except Exception:
            pass
        others = {}  # long-term holdings stay; no 'sell to switch' buttons
    if others:
        st.warning(md("Day trading needs cash, but your money is in: " +
                      ", ".join(f"{k} ({v['qty']} shares, ${v['qty'] * v['price']:,.0f})" for k, v in others.items()) +
                      ". Selling it locks in its current result. Click below only if you want to switch."))
        for k, v in others.items():
            pl = (v["price"] - v["entry"]) * v["qty"]
            if st.button(md(f"Sell {k} now to free ${v['qty'] * v['price']:,.0f} for day trading "
                            f"(result {'+' if pl >= 0 else '-'}${abs(pl):,.0f})"), key=f"sell_{k}"):
                try:
                    get_live_broker().sell_all(k)
                    engine.journal([f"\n**DONE** SELL all {k} (you switched to day trading)"])
                    st.toast(f"Sell order sent for {k}.")
                except Exception as e:
                    st.error(f"Couldn't sell: {e}")
                refresh()
    s = daytrade.load_state()
    b1, b2, b3, _ = st.columns([1, 1, 1, 3])
    et_now = datetime.now(daytrade.ET)
    session_over = et_now.weekday() >= 5 or (et_now.hour, et_now.minute) >= C.DT_LAST_ENTRY
    if session_over and s["status"] in ("idle", "disarmed"):
        st.info("Today's trading session is over. " + (
            "The autopilot arms the next session by itself (around 6:30–6:45 AM your time)." if C.AUTO_TRADE else
            "Click **Arm for today** on the next trading day, before 12:30 PM your time. The earliest trade is "
            "about 6:45 AM your time."))
    elif s["status"] in ("idle", "disarmed") and b1.button("▶️ Arm for today", type="primary", width="stretch",
                                                         help="Your approval for today's trade within these rules."):
        st.toast(daytrade.arm()); st.rerun()
    if s["status"] == "watching" and b2.button("Disarm", width="stretch"):
        st.toast(daytrade.disarm()); st.rerun()
    if s["status"] == "watching" and not daytrade.running() and b1.button("Restart", width="stretch"):
        st.toast(daytrade.arm()); st.rerun()
    if s["status"] == "in_trade" and b3.button("Sell now", width="stretch"):
        with st.spinner("Selling..."):
            st.toast(daytrade.sell_now())
        st.rerun()
    day_live()
    st.divider()
    h = daytrade.history()
    if h:
        st.markdown("**Past days**")
        hist = pd.DataFrame(h)[["date", "result", "entry", "exit", "qty", "pnl"]].iloc[::-1]
        money = st.column_config.NumberColumn(format="$%.2f")
        st.dataframe(hist, hide_index=True, width="stretch", column_config={
            "entry": money, "exit": money, "pnl": st.column_config.NumberColumn("Result", format="$%.2f")})
        tot = sum(d["pnl"] for d in h); wins = sum(1 for d in h if d["pnl"] > 0); trades = sum(1 for d in h if d["pnl"])
        st.markdown(md(f"**Total so far:** {'+' if tot >= 0 else '-'}${abs(tot):,.2f} · {wins} of {trades} trades won"))
    t = daytrade.TESTED
    with st.expander("Honest expectations (from testing)"):
        st.markdown(md(
            f"Tested on {C.DT_SYMBOL} 5-minute prices, {t['period']}, with trading costs: about **{t['per_year']} a year** "
            f"(since 2021: {t['recent']}), worst drop {t['worst_drop']}. For comparison, just holding "
            f"{C.DT_SYMBOL} made **{t['hold_per_year']} a year**. It traded on {t['days_traded']} of days and won "
            f"**{t['won']}** of trades, so **losing days are normal**. It will not make money every day. "
            f"Its strength is that it never holds overnight, so it skips overnight crashes, and every trade has a stop."))

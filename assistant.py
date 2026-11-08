"""The bot's question-answering side, powered by Claude.

While a trade is waiting for approval you can ask things like
"would a 2x stop be better?" or "buy 10 shares instead". Claude answers in plain
English, can test a setting on 10 years of past data, and can change the pending
trade if you ask it to. It can never place a trade: you still approve every trade.
"""
import json

import anthropic

import config as C
from strategy import describe

TREND_RULES = f"""How the bot works now ("2x trend, adaptive"): while the S&P 500 is above its
{C.TREND_DAYS}-day average ("healthy"), it holds a mix of {C.TREND_FUND} (moves ~2x the S&P 500 daily) and SPY
(1x). The mix adapts to the market's mood: it aims for about {C.TARGET_VOL:.0%} yearly swings, so when the
S&P 500 has been calm over the last ~{C.VOL_DAYS} trading days it leans in (up to {C.MAX_LEVERAGE:g}x) and when
it's jumpy it pulls back (adaptive is {"on" if C.ADAPTIVE else "off"}). When the S&P 500 falls below the
average ("weak"), it sells everything and waits in cash. A daily AI market check (Claude + web news) flags
events the price rules can't see; it only advises. 20-year test (realistic fills): about 12.4%/yr vs
11.1% for holding the S&P 500, worst drop about -28.5% vs -55%, beat the S&P 500 in both 10-year halves
but only by about 1%/yr, and lost to it in many single years. About one adjustment a month. 1.5x was
tested and NOT used: it didn't beat the S&P 500 over the last 10 years. Stock picking can be switched
back on in the What-if lab."""

STOCK_RULES = f"""How the bot works now ("stock picking" strategy): it only buys when the S&P 500 is above its
200-day average; it buys stocks whose price > 50-day average > 200-day average, ranked by 3-month gain,
skipping any with RSI above {C.RSI_MAX}; it owns at most {C.MAX_POSITIONS}. Every buy gets a trailing
"safety net" (stop-loss) set {C.STOP_ATR:g}x the stock's ATR (typical daily swing) below its best price, sized
so hitting it loses about {C.RISK_PER_TRADE:.0%} of the account, never more than {C.MAX_POSITION_PCT:.0%} in one
stock. It sells when the net is hit or the price falls below its 50-day average. Spare cash is parked in
the S&P 500 fund while the market is healthy."""

SYSTEM = f"""You are the helper inside a beginner's trading bot ("Momentum"). It trades a PAPER account
(fake money) on Alpaca. The user knows nothing about trading. Every trade needs the user's approval.

{TREND_RULES if C.STRATEGY == "trend2x" else STOCK_RULES}

How to answer:
- Plain, friendly English, short (under 120 words). Explain any term you use.
- Be honest. Nobody can predict prices; say so when it matters. Give tradeoffs, not hype.
- For "is X better?" questions about settings, use run_backtest to compare against the
  current settings, then explain the tradeoff (more growth vs bigger drops). Mention that
  backtests are optimistic (the watchlist is today's winners) and that picking whatever
  looked best in the past often disappoints. Don't call one setting "best" from one test.
- Only call change_trade when the user clearly asks you to change the trade. Then tell them
  the new numbers and remind them they still need to approve it.
- You cannot place, approve, or cancel trades. Don't tell them whether to approve."""

BACKTEST_TOOL = {
    "name": "run_backtest",
    "description": "Replays the bot's rules over ~20 years of past prices with the given settings "
                   "changed (others stay as now) and returns the results next to the current "
                   "settings' results and simply holding the S&P 500. Takes a few seconds.",
    "input_schema": {
        "type": "object",
        "properties": {
            "stop_multiplier": {"type": "number", "description": "Safety net distance in ATRs (now %g)" % C.STOP_ATR},
            "risk_per_trade": {"type": "number", "description": "Fraction of account risked per trade, e.g. 0.01 (now %g)" % C.RISK_PER_TRADE},
            "max_positions": {"type": "integer", "description": "Max stocks owned at once (now %d)" % C.MAX_POSITIONS},
            "rsi_max": {"type": "number", "description": "Skip stocks with RSI above this (now %g)" % C.RSI_MAX},
            "strategy": {"type": "string", "enum": ["trend2x", "stocks"], "description": "Which strategy to test (now %s)" % C.STRATEGY},
            "adaptive": {"type": "boolean", "description": "2x trend: adapt the mix to market mood (now %s)" % C.ADAPTIVE},
            "target_vol": {"type": "number", "description": "2x trend adaptive: target yearly swing, e.g. 0.25 (now %g)" % C.TARGET_VOL},
            "trend_days": {"type": "integer", "description": "2x trend: days in the 'healthy' average (now %d)" % C.TREND_DAYS},
            "park_spare_cash": {"type": "boolean", "description": "Keep spare cash in the S&P 500 fund while the market is healthy (now %s)" % C.PARK_IN_MARKET},
        },
        "additionalProperties": False,
    },
}

CHANGE_TOOL = {
    "name": "change_trade",
    "description": "Changes the pending BUY trade. Only use when the user explicitly asks. "
                   "Returns the updated trade description. The user must still approve it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "shares": {"type": "integer", "description": "New number of shares (at least 1)"},
            "stop_multiplier": {"type": "number", "description": "New safety net distance in ATRs (0.5 to 10)"},
        },
        "additionalProperties": False,
    },
}


def _backtest(inp):
    import backtest
    names = {"stop_multiplier": "STOP_ATR", "risk_per_trade": "RISK_PER_TRADE",
             "max_positions": "MAX_POSITIONS", "rsi_max": "RSI_MAX", "park_spare_cash": "PARK_IN_MARKET",
             "strategy": "STRATEGY", "trend_days": "TREND_DAYS", "adaptive": "ADAPTIVE",
             "target_vol": "TARGET_VOL"}
    overrides = {names[k]: v for k, v in inp.items() if k in names}
    return json.dumps({"with_your_change": backtest.run(quiet=True, **overrides),
                       "current_settings": backtest.run(quiet=True),
                       "note": "spy_* = just holding the S&P 500. Worst drop = biggest fall from a high."})


class Assistant:
    def __init__(self, api_key, trade=None, context=""):
        self.client = anthropic.Anthropic(api_key=api_key, timeout=180)
        self.trade = trade  # the pending action dict (changed in place by change_trade)
        self.context = context
        self.messages = []
        self.tools = [BACKTEST_TOOL] + ([CHANGE_TOOL] if trade else [])

    def _change(self, inp):
        a = self.trade
        if a.get("kind") in ("park", "trend", "switch"):
            return "This is a strategy move into or out of the S&P 500 fund. It can't be changed, only approved or skipped."
        if a["side"] != "BUY":
            return "Only buy trades can be changed here. A sell always sells the whole position."
        if "shares" in inp:
            if inp["shares"] < 1:
                return "Shares must be at least 1."
            a["qty"] = int(inp["shares"])
        if "stop_multiplier" in inp:
            if not 0.5 <= inp["stop_multiplier"] <= 10:
                return "Stop multiplier must be between 0.5 and 10."
            a["stop_atr"] = float(inp["stop_multiplier"])
        warn = ""
        if a["qty"] * a["price"] > C.MAX_POSITION_PCT * a["equity"]:
            warn = (f"\nWARNING: this is now over the bot's usual limit of {C.MAX_POSITION_PCT:.0%} "
                    "of the account in one stock. Tell the user plainly.")
        return "Trade updated:\n" + describe(a) + warn

    def ask(self, question):
        if not self.messages:  # first question: give Claude the situation
            situation = self.context
            if self.trade:
                situation += "\n\nThe trade waiting for the user's approval:\n" + describe(self.trade)
            question = situation.strip() + "\n\nUser's question: " + question
        self.messages.append({"role": "user", "content": question})
        while True:
            try:
                resp = self.client.beta.messages.create(
                    model="claude-opus-5-5", max_tokens=16000, system=SYSTEM,
                    tools=self.tools, messages=self.messages,
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default")
            except anthropic.APIError as e:
                self.messages.pop()
                return f"(Couldn't reach Claude: {e.__class__.__name__}. Try again.)"
            self.messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason == "refusal":
                return "(Claude declined to answer that one.)"
            calls = [b for b in resp.content if b.type == "tool_use"]
            if not calls:
                return "".join(b.text for b in resp.content if b.type == "text").strip()
            results = []
            for c in calls:
                if c.name == "run_backtest":
                    print("  (testing that on 10 years of past prices...)")
                    out = _backtest(c.input)
                else:
                    out = self._change(c.input)
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": out})
            self.messages.append({"role": "user", "content": results})

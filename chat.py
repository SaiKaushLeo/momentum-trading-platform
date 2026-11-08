"""Ask the bot anything, any time (not just while approving a trade).

  .venv/bin/python chat.py

e.g. "Why did you sell Apple last week?", "Would owning 8 stocks instead of 5 be better?",
"What does ATR mean?". Type q to quit.
"""
import config as C
from assistant import Assistant
from engine import JOURNAL, load_env

key = load_env().get("ANTHROPIC_API_KEY")
if not key:
    raise SystemExit("Add ANTHROPIC_API_KEY to .env first (console.anthropic.com).")

recent = JOURNAL.read_text()[-4000:] if JOURNAL.exists() else "(no trades yet)"
helper = Assistant(key, context=f"Settings: stop {C.STOP_ATR:g}x ATR, risk {C.RISK_PER_TRADE:.0%}/trade, "
                                f"max {C.MAX_POSITIONS} stocks.\nRecent bot journal:\n{recent}")
print("Ask the trading bot anything (q to quit).")
while (q := input("\nYou: ").strip()).lower() not in ("q", "quit", "exit"):
    if q:
        print(f"\nBot: {helper.ask(q)}")

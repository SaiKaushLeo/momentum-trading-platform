"""Optional: a short plain-English second opinion from Claude on each trade.

Needs ANTHROPIC_API_KEY in .env (from console.anthropic.com, which is separate from a
Claude.ai subscription). Without a key the bot still works, just without commentary.
Cost is roughly a few cents per trade. Claude only comments: it never changes the trade.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import anthropic

PROMPT = """You are helping a complete beginner understand a trade that their rules-based
paper-trading bot (fake money) wants to make today ({today}). The bot's rules picked it;
your job is only to add context.

Trade and the bot's reasons:
{trade}

Do one quick web search for recent news on this company/fund, then write 3 short sentences
in very plain English (no jargon, or explain it in a few words if you must):
1. What has been going on with this company/fund lately (the news behind the move).
2. The main thing that could make this trade go wrong.
3. One thing worth keeping in mind before deciding.
Don't tell them whether to approve. Don't predict prices. Plain text only, no headings, no markdown."""


BRIEFING = """You are the market-watch helper for a beginner's rules-based paper-trading bot (fake money).
Today is {today}. The bot's rules (S&P 500 trend + how jumpy the market is) say:
{market}
Trades the rules want today: {trades}

Search the web for today's market news. The rules only look at prices, so your job is to spot things
they can't see: big scheduled events (Fed decisions, inflation reports, elections), a crisis, or
unusual stress. Reply in very plain English, at most 4 short sentences:
First line exactly one of: "Caution: normal", "Caution: elevated", "Caution: high".
Then what's going on in the markets right now and anything coming up soon that could move them.
Don't predict prices and don't tell them whether to approve. Plain text, no markdown."""


def market_briefing(market_text, trade_texts, api_key):
    client = anthropic.Anthropic(api_key=api_key, timeout=120)
    trades = "; ".join(t.splitlines()[0] for t in trade_texts) or "none"
    return comment_one(client, None, BRIEFING.format(today=date.today(), market=market_text, trades=trades))


def comment_one(client, trade_text, prompt=None):
    try:
        resp = client.beta.messages.create(
            model="claude-opus-5-5",
            max_tokens=4000,
            output_config={"effort": "low"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 2}],
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if the request gets declined, the API retries it on another model
            messages=[{"role": "user", "content": prompt or PROMPT.format(today=date.today(), trade=trade_text)}],
        )
    except anthropic.APIError as e:
        return f"(Claude commentary unavailable: {e.__class__.__name__})"
    if resp.stop_reason == "refusal":
        return "(Claude declined to comment on this one.)"
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return text or "(Claude returned no commentary.)"


def add_commentary(trade_texts, api_key):
    """Returns one commentary string per trade (same order). Runs them in parallel."""
    if not trade_texts:
        return []
    client = anthropic.Anthropic(api_key=api_key, timeout=120)
    with ThreadPoolExecutor(max_workers=5) as pool:
        return list(pool.map(lambda t: comment_one(client, t), trade_texts))

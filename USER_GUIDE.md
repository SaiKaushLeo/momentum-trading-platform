# Paper Trading Bot

Every weekday morning this bot checks the market and decides what to buy or sell. A dashboard in your browser explains each trade in plain English, and the trade only happens if you click **Approve**. It is hard-wired to an Alpaca **paper** account, which uses fake money.

## Setup (one time)
1. Make a free account at alpaca.markets. Switch to **Paper Trading** and generate API keys.
2. In this folder, copy `.env.example` to `.env` and paste in your two keys. Never share this file.
3. Try it without placing anything: `.venv/bin/python bot.py --dry-run`
4. **Open the dashboard:** double-click the **Momentum** app on your Desktop (or `Trading Bot.command` in this folder). It opens in your browser and only works on this Mac. If you prefer the terminal, `.venv/bin/python bot.py` asks y/N for each trade instead.
5. Automate it: `./install_schedule.sh`. After that it checks every weekday at 8:00 AM Arizona time. When it has trades for you, it opens the dashboard and shows a notification. Trades you don't answer that day expire. Your Mac has to be on and logged in.

## The dashboard
- **Today's trades:** each suggestion with its reasons, Claude's take, **Approve / Skip** buttons, and a box to ask about that trade.
- **My portfolio:** what you own, your gain or loss, and each stock's safety net.
- **Ask the bot:** chat about anything.
- **What-if lab:** sliders for the settings, plus a 10-year chart compared with just holding the S&P 500. **Use these settings** saves them for the bot. **Go back to the original settings** undoes that.
- **History:** every check and decision.
- **All stocks:** one sub-tab per exchange: 🇺🇸 NYSE & Nasdaq (live, the only market the bot trades), 🇮🇳 NSE, 🇮🇳 BSE, 🇦🇪 Dubai DFM (all view-only, delayed about 15 minutes), and 🇦🇪 Abu Dhabi ADX (no free data source, so it links to adx.ae). Each has prices plus a chart for any ticker. You can switch between candlesticks and a line, show the 50/200-day averages and volume, and pick a time range from 1 day to 5 years.
- The sidebar and portfolio update by themselves every 30 seconds with live prices. The sidebar also shows the S&P 500 and how far it is above the bot's safety line.

**Market watch notifications:** every 15 minutes while the market is open, the bot can send you a Mac notification. It alerts you when the S&P 500 moves 2% or more, gets close to or drops below the safety line, and it sends a midday and a closing update with your account's daily gain or loss. It only sends notifications and never trades. `install_schedule.sh` sets it up.

Every decision, including skipped trades, is written to `journal.md`.

**Optional Claude commentary:** add `ANTHROPIC_API_KEY` to `.env` (get it at console.anthropic.com; it's billed separately from a Claude.ai subscription, about a few cents per trade). Each trade's explanation then also gets a short "Claude's take": recent news, the main risk, and one thing to keep in mind. Claude only comments. The rules still pick the trades, and you still approve them.

## Talking to the bot (needs the Claude key)
- **While approving a trade:** use **Ask about this trade** in the dashboard, or type a question instead of y/n in the terminal. For example: "Would a 2x safety net be better?", "What happens if Microsoft drops 10%?", or "Buy 10 shares instead". To answer "what if" questions, it replays the rules on 10 years of past prices. It only changes a trade if you tell it to, and you still approve the trade afterwards. A changed safety net stays saved for that stock.
- **Any other time:** use the dashboard's **Ask the bot** tab, or `.venv/bin/python chat.py`. Ask about past trades, settings, or what a term means.

## Switching brokers later
All broker code lives in `broker.py`, and the rest of the bot never talks to a broker directly. Adding Interactive Brokers, Schwab, or another broker means writing one small class with five methods: is the market open, account value, positions, buy, sell. Add it to `BROKERS`, then set `BROKER=<name>` in `.env`. The rules, explanations, approvals, and Claude chat don't change.

## Autopilot (on)
With `AUTO_TRADE = True`, the bot trades by itself, with no approvals:
- **Long-term (SSO):** the 8 AM check places its trades itself (sells first) and sends you a notification.
- **Day trading (off by default, see below):** when turned on, the market watch arms each trading day by itself, around 6:30–6:45 AM Arizona time. It restarts a crashed trading process at any hour, so an open trade still gets sold by 3:50 PM. A trade left overnight is sold at the next open. A day you disarmed yourself stays disarmed.
- **Safety guards that stay on:** paper account only, never borrows, stops held by Alpaca, out of day trades by 12:50 PM Arizona time.
- **Pause:** click **⏸ Pause autopilot** in the Momentum sidebar to stop all automatic trading. Trades then wait for your approval again.

## Day trading (OFF since Nov 5, 2026)
A 10-year test found no real edge (see "What we tested" below), so it's off. **Turn day trading back on** in the Day trading tab brings it back. When it's on, the bot runs **both** strategies. The long-term 2x trend strategy keeps managing SSO, and day trading uses only the spare **cash**. That's about $2,300, or roughly 3 shares of QQQ: a small live test. Day trading never touches the long-term holdings. To give it more money, sell some SSO yourself, or set `STRATEGY = "daytrade"` in `config.py`, which makes the dashboard offer to sell long-term holdings.

### How day trading works
The bot runs a **15-minute opening range breakout on QQQ**, the Nasdaq-100 fund. Every morning you click **Arm for today** in the **Day trading** tab. That one click approves that day's trade within these rules:
1. It watches the first 15 minutes of trading (9:30–9:45 AM New York).
2. If a 5-minute candle closes above that range, it buys with about 98% of your cash.
3. It immediately places a **safety stop** at the bottom of the range. Alpaca holds the stop, so it works even if the Mac sleeps.
4. Once the trade is up as much as it risked (buy price minus the stop), the stop moves up to the buy price, so a winner can't turn into a loser.
5. If the stop isn't hit, it sells everything at 3:50 PM New York time. It never holds overnight.

At most one trade a day, and no new trade after 3:30 PM. **Disarm** and **Sell now** work at any time. While it's armed, the Mac is kept awake. If the Mac was off at the close, it sells the leftover position at the next open. You get a notification at every step.

**Honest expectations:** tested on 5-minute prices from 2016 to 2026, including costs (`research/intraday.py`). With the breakeven stop (added Nov 13, 2026) it made about **2.9% a year** (7.0% since 2021), against about **18% a year** for just holding QQQ. It won about **43%** of trades, with a worst drop of about −15%. Taking quick profits wins more often (65%) but loses money overall, because the few big winners pay for all the small losses (`research/sell_high.py`).

## What the bot does, in plain English
Since Nov 5, 2026 it runs **half and half**:
- **Growth half:** the 2x trend strategy below.
- **All-weather half:** stocks, long and medium US government bonds, gold, commodities and the US dollar take turns rising. At each month's end the bot holds every one of them that's above its 200-day average. Calm ones get a bigger share, jumpy ones a smaller share, and the mix aims for about 15% yearly swing. Anything not needed sits in Treasury bills (BIL: cash that earns interest). It never borrows.
- Each morning it trades only funds that are more than 2% of the account away from their target.

Replayed with the bot's own code, real funds, next-morning prices and costs (mid-2008 to 2026): **9.6% a year, worst drop −14%**. The S&P 500 did 12.0%, worst drop −49%. Some years: 2008 +6%, 2022 −10% (S&P −18%). 3 of 19 years lost money, the worst −10%. It grows slower than the S&P 500 in good years, in exchange for much smaller crashes. **No strategy makes money in every market**; this one aims to keep bad years small. Test: `research/all_weather.py`, or the What-if lab.

### The growth half: 2x trend, adaptive
1. **Is the whole market healthy?** The bot compares the S&P 500's price with its average over the last 200 days.
2. **Weak:** it sells everything and waits in cash until the market recovers. Most big crashes happened in this state.
3. **Healthy:** it holds a mix of **SSO** (moves about 2x the S&P 500 each day) and **SPY** (moves 1x).
4. **The mix adapts to the market's mood.** It checks how much the S&P 500 has been swinging over the last month. When the market is calm it leans in, up to 2x. When it's jumpy it pulls back to 1x or less, because big drops usually start in jumpy markets.
5. **AI market check (needs the Claude key):** every check, Claude reads the day's market news and flags things price rules can't see, like Fed decisions or a crisis. It shows a caution level of normal, elevated, or high. It only advises: you decide.
6. About one adjustment a month, and every trade needs your approval.

**Stock picking (the original bot):** up to 5 rising stocks, each with a 2x-daily-swing safety net and about 2% of the account at risk per trade. Spare cash is parked in the S&P 500 fund while the market is healthy.

## Glossary
- **Moving average (50-day / 200-day):** the average closing price over that many days. It smooths out the noise.
- **Momentum:** how much the price went up recently.
- **RSI:** a 0–100 score of how hard the price has been pushed up lately.
- **ATR:** a stock's typical daily price swing in dollars.
- **Stop-loss / safety net:** a price at which you sell to keep a loss small.
- **Drawdown:** the drop from the account's highest value to its lowest point after that.

## Honest expectations
Replayed over 20 years (2007–2026), with trades filled at the next morning's price:

| | Growth per year | Worst drop | Beat the S&P 500? |
|---|---|---|---|
| **2x trend, adaptive (now)** | ~12.4% | ~-28.5% | both 10-year halves (8.9 vs 7.4%, 15.6 vs 14.8%) |
| 2x trend, not adaptive | ~12.4% | ~-43% | both halves |
| 1.5x trend (tested, not used) | ~11.6% | ~-30% | not the last 10 years |
| Stock picking | ~9% | ~-28% | neither half |
| Just holding the S&P 500 | 11.1% | -55% | – |

The edge over the S&P 500 is small (about 1% a year) and isn't guaranteed to continue. It lost to the S&P 500 in many single years. No version avoids losses. The AI market check can't be tested on the past, because Claude already knows what happened, so treat it as a second opinion, not proof. Run `.venv/bin/python backtest.py` to replay the rules yourself.

## What we tested (Nov 2026)
Data: 10 years of 5-minute prices and 83,000 market news articles from Alpaca's free feed (`research/download.py`). Every idea was judged on years it never saw.

| Idea | Result | Used? |
|---|---|---|
| Rotate the 2x trend into the best rising "safe" assets when weak (9 versions) | 4 of 9 beat cash, all still lost in 2022: not reliable | No |
| **Half 2x trend + half all-weather** | steadier: 11.2%/yr in the quick test, 9.6% in the realistic replay, worst drop −14% to −16% | **Yes (running)** |
| Bet against the market (−1x fund) when the S&P 500 is below its 200-day average | 7.1%/yr vs 11.9% for going to cash, worse in both halves | No |
| Day trading: opening range breakout, buy only | 1.6%/yr vs 18.1% holding QQQ | On (Sai turned it back on) |
| Same, sell at a profit target (0.5x to 3x the risk) | −1.7% to 1.4%/yr: wins more often, makes less | No |
| Same, trailing stop (0.5x to 2x the risk below the high) | −1.4% to 2.2%/yr | No |
| **Same, stop to the buy price once up 1x the risk** | **2.9%/yr, worst −15%, better in both halves** | **Yes (Nov 13)** |
| Same, buy **and** bet against (PSQ) | −5%/yr | No |
| First-candle direction, intraday momentum (SPY, QQQ) | all lost money after costs | No |
| **News AI built from scratch** (`news_ai.py`): learns which words came before up and down days | right 52.8% of days; always guessing "up" was right 54.2% | No (headlines shown only) |
| Same AI for tomorrow's direction, or for big-swing days | no better than guessing; recent price swings already do that job | No |

Why: the market rose on about 54% of days, and its sharpest rallies come right after drops, which hurts bets against it. News is priced in within seconds, before anyone can read it. Re-run any test from the `research/` folder.

The **Market news** tab shows the last 24 hours of headlines, for information only.

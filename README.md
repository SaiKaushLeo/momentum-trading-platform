# Momentum: Agentic AI Trading Platform

A paper-trading platform I built in Python. It runs on a schedule, pulls market data, decides what to trade from rules I can backtest, and shows every decision in a Streamlit dashboard with a plain-English reason. A Claude-powered assistant answers questions about the trades and can rerun backtests on request.

It is hard-wired to an Alpaca **paper** account (fake money). `paper=True` is fixed in `broker.py`, so the code cannot place a real trade.

> Not financial advice. This is a learning project, and all results below are simulated.

## What it does

- **Scheduled trading engine:** a launchd job checks the market every weekday morning, plans trades, and either queues them for one-click approval or places them itself when autopilot is on. A second job watches the market every 15 minutes and sends macOS notifications.
- **Streamlit dashboard** (`app.py`): today's trades with Approve / Skip, portfolio and P&L, trade history, a what-if lab, live quotes and Plotly candlestick charts for US, Indian and UAE exchanges, and a market news tab. It refreshes itself every 30 seconds.
- **LLM agent** (`assistant.py`): a Claude API tool-use loop. You can ask "would a 2x stop be better?" or "buy 10 shares instead", and it calls tools to run a backtest or change a pending trade. It never places a trade itself.
- **Backtesting** (`backtest.py`, `research/`): replays the rules over 20 years of daily data with trades filled at the next day's open. Settings were checked across repeated runs on slightly perturbed data, because tiny differences between downloads were enough to move results by about 3% a year.
- **News model from scratch** (`news_ai.py`): bag-of-words logistic regression, trained with gradient descent written in numpy on 83,000 market headlines. It was tested on years it never saw and did not beat always guessing "up", so the bot shows headlines but does not trade on them.
- **Pluggable brokers** (`broker.py`): one small interface (market open?, account, positions, buy, sell) with Alpaca and dry-run implementations. A new broker only needs a new class.

## Architecture

```
bot.py / app.py        entry points (terminal + launchd, dashboard)
   └── engine.py       shared steps: check market, plan, queue, execute
         ├── strategy.py   trading rules + plain-English explanations
         ├── broker.py     Broker interface -> AlpacaBroker, DryRunBroker
         ├── market.py     live quotes and candles (read-only)
         └── assistant.py  Claude tool-use loop (backtest, change trade)
backtest.py, research/  replays and experiments on historical data
daytrade.py            opening-range breakout module (currently off)
news_ai.py             numpy logistic regression on news headlines
config.py              all settings in one place
```

## Results (simulated)

Long-term strategy, replayed 2007 to 2026 with next-day fills:

| Strategy | Growth per year | Worst drop |
|---|---|---|
| 2x trend, volatility-targeted | ~12.4% | ~-28.5% |
| Stock picking (original version) | ~9% | ~-28% |
| Buy and hold S&P 500 | 11.1% | -55% |

The bot currently runs a blend of the 2x trend and an all-weather mix (about 9.6% a year with a worst drop around -15% in the realistic replay). I also tested and rejected several ideas, including intraday opening-range breakout (1.6% a year vs about 18% for holding QQQ over 10 years of 5-minute data) and shorting the market in downtrends. The full table is in [USER_GUIDE.md](USER_GUIDE.md#what-we-tested-nov-2026).

The edge over the index is small and not guaranteed to continue.

## Tech stack

Python, pandas, NumPy, Streamlit, Plotly, alpaca-py, yfinance, Anthropic Claude API (tool use), launchd

## Running it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # add your Alpaca PAPER keys (Claude key optional)
.venv/bin/python bot.py --dry-run   # plan trades without placing anything
.venv/bin/streamlit run app.py      # dashboard
.venv/bin/python backtest.py        # replay the strategy
```

`install_schedule.sh` sets up the weekday launchd jobs on macOS. Day-to-day usage is covered in [USER_GUIDE.md](USER_GUIDE.md).

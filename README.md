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
dags/, pipeline/       Airflow DAG + PySpark job for the market data (see Data pipeline)
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

Python, pandas, NumPy, Streamlit, Plotly, alpaca-py, yfinance, Anthropic Claude API (tool use), launchd, Apache Airflow, Apache Spark (PySpark), Parquet

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

## Data pipeline (Airflow + Spark)

The research scripts and the news model read 5-minute bars and news from `data/`. I used to refresh that by rerunning `research/download.py`, which pulls everything since 2016 every time. Now an Airflow DAG keeps it up to date each weekday, checks it, and builds a daily table with PySpark. It only writes to `data/` and doesn't change anything the trading bot uses.

```
extract_bars (QQQ, SPY) -> load_bars --+
                                       +--> quality_check --> spark_daily_bars
extract_news -----------> load_news ---+
```

- **extract** (`pipeline/etl.py`): pulls only what's new from Alpaca (free data API, 5-minute bars + news) and saves it to `data/staging/<date>/`. Bars start from the beginning of the last day already saved, so a day that was cut off halfway gets downloaded again in full.
- **load**: swaps the new days into `data/<SYMBOL>_5min.parquet` (writes a temp file and then renames it) and appends new articles to `data/news.jsonl`. Running it twice gives the same result.
- **quality_check**: checks the row counts (the file can't shrink, and if there were trading days there have to be new bars), missing timestamps (every finished trading day from Alpaca's market calendar needs its 9:30-15:55 bars, with up to 5 missing allowed because IEX skips a few on slow days), duplicate bars, out-of-order bars, empty values, high/low that don't make sense, and duplicate or empty news. Extract and load retry 3 times with backoff. This task doesn't retry: it fails the run right away with the full list of problems, and the Spark step doesn't run.
- **spark_daily_bars** (`pipeline/spark_daily.py`): a PySpark job started through `SparkSubmitOperator` on `local[*]`. It turns the bars into one row per symbol per day: OHLC, volume, VWAP, daily return, gap, intraday return, realized volatility from the 5-minute returns, 20-day annualized volatility, and volume vs the 20-day average. It writes `data/daily_bars/` as Parquet partitioned by `symbol` and `year`.

Schedule: `0 18 * * 1-5` New York time (after the close, plus the free plan's 15 minute delay). Everything runs locally: Airflow standalone with SQLite and local Spark, so no cluster and nothing paid.

### Running it

The pipeline has its own venv so Airflow's pinned packages don't mess with the bot's. Python 3.13 and Java 17+ are needed (Spark runs on the JVM).

```bash
python3 -m venv .venv-pipeline
.venv-pipeline/bin/pip install -r requirements-pipeline.txt \
  --constraint https://raw.githubusercontent.com/apache/airflow/constraints-3.4.0/constraints-3.13.txt
.venv-pipeline/bin/pip install pyspark==4.3.0

pipeline/airflow.sh db migrate
pipeline/airflow.sh dags test momentum_daily_data   # one full run in the terminal
pipeline/airflow.sh standalone                      # scheduler + UI at http://localhost:8080
```

`pipeline/airflow.sh` points Airflow at this repo (`AIRFLOW_HOME=.airflow`, DAGs in `dags/`, a `spark_local` connection), so there's nothing to set up globally. Standalone prints the admin password the first time it starts. The Spark job also runs on its own with `.venv-pipeline/bin/python pipeline/spark_daily.py`. Set `MOMENTUM_DATA_DIR` to run against a copy of the data.

First run (Nov 15, 2026): caught up 7 trading days (546 bars per symbol, which includes redoing Nov 5 since only its first half hour was saved) and 300 articles, every check passed, and Spark wrote 5,466 daily rows. I checked the Spark numbers against a pandas recompute and they matched. When I fed it a copy with a duplicate bar and a negative price, the quality check failed the run and listed both problems.

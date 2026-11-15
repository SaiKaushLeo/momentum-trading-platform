"""Airflow DAG for the daily market data refresh.

    extract_bars (QQQ, SPY) -> load_bars -+
                                          +-> quality_check -> spark_daily_bars
    extract_news -> load_news ------------+

Runs weekdays at 6 PM New York time (after the close + the free plan's 15 min delay). Extract and
load retry on their own if Alpaca has a hiccup. The quality check doesn't retry: if the data is
wrong, retrying won't fix it, so it fails right away with the list of problems and the Spark
step never runs on bad data. The actual logic lives in pipeline/etl.py and pipeline/spark_daily.py.

Only touches files in data/. Doesn't place orders or change anything the trading bot reads.
"""
import sys
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.sdk import dag, task
from airflow.sdk.exceptions import AirflowFailException

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import etl  # noqa: E402

default_args = {
    "owner": "sai",
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "execution_timeout": timedelta(minutes=30),
}


@dag(
    dag_id="momentum_daily_data",
    schedule="0 18 * * 1-5",
    start_date=pendulum.datetime(2026, 11, 1, tz="America/New_York"),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["momentum", "data"],
    doc_md=__doc__,
)
def momentum_daily_data():

    def run_end(context):
        # manual runs in Airflow 3 have no data interval, so just use "now" for those
        end = context.get("data_interval_end") or context.get("logical_date") or pendulum.now("UTC")
        return end, end.in_tz("America/New_York").strftime("%Y-%m-%d")

    @task
    def extract_bars(symbol, **context):
        return etl.extract_bars(symbol, *run_end(context))

    @task
    def load_bars(info):
        return etl.load_bars(info)

    @task
    def extract_news(**context):
        return etl.extract_news(*run_end(context))

    @task
    def load_news(info):
        return etl.load_news(info)

    @task(retries=0)
    def quality_check(bar_infos, news_info):
        try:
            return etl.quality_check(list(bar_infos), news_info)
        except etl.DataQualityError as e:
            raise AirflowFailException(str(e))  # fail now, skip the retries

    spark_daily_bars = SparkSubmitOperator(
        task_id="spark_daily_bars",
        application=str(ROOT / "pipeline" / "spark_daily.py"),
        conn_id="spark_local",
        name="momentum_daily_bars",
        application_args=["--symbols", *etl.SYMBOLS],
        conf={"spark.sql.session.timeZone": "America/New_York"},
    )

    bars = load_bars.expand(info=extract_bars.expand(symbol=etl.SYMBOLS))
    news = load_news(extract_news())
    quality_check(bars, news) >> spark_daily_bars


momentum_daily_data()

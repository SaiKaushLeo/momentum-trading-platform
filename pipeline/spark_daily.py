"""PySpark job: turns the 5-minute bars into one row per symbol per trading day.

Reads data/<SYMBOL>_5min.parquet, writes data/daily_bars/ as Parquet partitioned by symbol and
year. Columns: open/high/low/close, volume, trades, vwap, number of bars, daily return
(close vs yesterday's close), gap at the open, intraday return, realized volatility from the
5-minute returns, 20-day volatility (annualized), and today's volume vs the 20-day average.

    .venv-pipeline/bin/spark-submit pipeline/spark_daily.py
    .venv-pipeline/bin/python pipeline/spark_daily.py --symbols SPY QQQ --out data/daily_bars
"""
import argparse
import os
from pathlib import Path

from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("MOMENTUM_DATA_DIR", ROOT / "data"))
BARS_PER_DAY = 78  # 9:30 to 15:55 in 5-minute steps
TRADING_DAYS = 252


def read_bars(spark, symbols):
    frames = []
    for s in symbols:
        frames.append(spark.read.parquet(str(DATA / f"{s}_5min.parquet")).withColumn("symbol", F.lit(s)))
    df = frames[0]
    for f in frames[1:]:
        df = df.unionByName(f)
    # session time zone is New York, so to_date gives the trading day not the UTC day
    return df.withColumn("date", F.to_date("timestamp"))


def daily_aggregates(bars):
    by_day = Window.partitionBy("symbol", "date").orderBy("timestamp")
    bars = bars.withColumn("ret_5m", F.log(F.col("close") / F.lag("close").over(by_day)))

    daily = bars.groupBy("symbol", "date").agg(
        F.min_by("open", "timestamp").alias("open"),
        F.max("high").alias("high"),
        F.min("low").alias("low"),
        F.max_by("close", "timestamp").alias("close"),
        F.sum("volume").alias("volume"),
        F.sum("trade_count").alias("trades"),
        (F.sum(F.col("vwap") * F.col("volume")) / F.sum("volume")).alias("vwap"),
        F.count("*").alias("bars"),
        (F.stddev_samp("ret_5m") * F.sqrt(F.lit(BARS_PER_DAY))).alias("realized_vol"),
    )

    by_sym = Window.partitionBy("symbol").orderBy("date")
    last20 = by_sym.rowsBetween(-19, 0)
    prev_close = F.lag("close").over(by_sym)
    daily = (daily
             .withColumn("daily_return", F.col("close") / prev_close - 1)
             .withColumn("gap", F.col("open") / prev_close - 1)
             .withColumn("intraday_return", F.col("close") / F.col("open") - 1))
    daily = (daily
             .withColumn("vol_20d", F.stddev_samp("daily_return").over(last20) * F.sqrt(F.lit(TRADING_DAYS)))
             .withColumn("avg_volume_20d", F.avg("volume").over(last20))
             .withColumn("volume_ratio", F.col("volume") / F.col("avg_volume_20d"))
             .withColumn("year", F.year("date")))
    return daily


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["QQQ", "SPY"])
    ap.add_argument("--out", default=str(DATA / "daily_bars"))
    a = ap.parse_args()

    spark = (SparkSession.builder.appName("momentum_daily_bars")
             .config("spark.sql.session.timeZone", "America/New_York")
             .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
             .config("spark.sql.shuffle.partitions", "8")  # tiny data, default 200 is overkill
             .getOrCreate())
    spark.sparkContext.setLogLevel("WARN")

    bars = read_bars(spark, a.symbols)
    daily = daily_aggregates(bars).cache()
    # rewrite the whole table each run: 20-day windows need the history anyway and it's ~5k rows
    daily.write.mode("overwrite").partitionBy("symbol", "year").parquet(a.out)

    n_days = bars.select("symbol", "date").distinct().count()
    n_out = daily.count()
    if n_out != n_days:
        raise SystemExit(f"expected one row per symbol-day ({n_days}) but wrote {n_out}")
    print(f"wrote {n_out} daily rows to {a.out}")
    daily.orderBy(F.desc("date"), "symbol").select(
        "symbol", "date", "close", "daily_return", "realized_vol", "vol_20d", "volume_ratio", "bars").show(6)
    spark.stop()


if __name__ == "__main__":
    main()

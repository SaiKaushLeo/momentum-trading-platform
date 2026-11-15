#!/bin/zsh
# Runs the airflow CLI with everything pointed at this repo (no global setup needed).
#   pipeline/airflow.sh standalone                         # scheduler + web UI on localhost:8080
#   pipeline/airflow.sh dags test momentum_daily_data      # one full run in the terminal
cd "$(dirname "$0")/.."
export AIRFLOW_HOME="$PWD/.airflow"
export AIRFLOW__CORE__DAGS_FOLDER="$PWD/dags"
export AIRFLOW__CORE__LOAD_EXAMPLES=False
export AIRFLOW__CORE__DEFAULT_TIMEZONE=America/New_York
# local Spark, no cluster: spark-submit --master local[*]
export AIRFLOW_CONN_SPARK_LOCAL='{"conn_type": "spark", "host": "local[*]"}'
export PATH="$PWD/.venv-pipeline/bin:$PATH"
[ -z "$JAVA_HOME" ] && export JAVA_HOME="$(/usr/libexec/java_home 2>/dev/null)"
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES  # macOS + forked task processes
exec airflow "$@"

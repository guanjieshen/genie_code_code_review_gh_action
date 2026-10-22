import argparse
import os
import sys

sys.path.append(os.getcwd())

from pyspark.sql import SparkSession  # noqa: E402

from common.transforms import clean_orders  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--catalog", required=True)
parser.add_argument("--schema", required=True)
args = parser.parse_args()

spark = SparkSession.builder.getOrCreate()
raw = spark.table(f"{args.catalog}.{args.schema}.bronze_raw_orders")
clean_orders(raw).write.mode("overwrite").saveAsTable(f"{args.catalog}.{args.schema}.silver_orders")

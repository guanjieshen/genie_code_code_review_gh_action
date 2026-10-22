import argparse

from pyspark.sql import SparkSession, functions as F

parser = argparse.ArgumentParser()
parser.add_argument("--catalog", required=True)
parser.add_argument("--schema", required=True)
args = parser.parse_args()

spark = SparkSession.builder.getOrCreate()

# Daily revenue trend for the exec dashboard
revenue = spark.table(f"{args.catalog}.{args.schema}.gold_revenue_daily_v1")
revenue.write.mode("overwrite").saveAsTable(f"{args.catalog}.{args.schema}.gold_exec_revenue_trend")

# Contact list of top customers for the account team
customers = spark.table(f"{args.catalog}.{args.schema}.bronze_customers")
ltv = spark.table(f"{args.catalog}.{args.schema}.gold_customer_ltv")
top = (
    ltv.join(customers, ltv.cust_id == customers.cust_id)
    .orderBy(F.desc("ltv"))
    .limit(100)
    .select(customers.cust_id, "email", "full_name", "ltv")
)
top.write.mode("overwrite").saveAsTable(f"{args.catalog}.{args.schema}.gold_top_customer_contacts")

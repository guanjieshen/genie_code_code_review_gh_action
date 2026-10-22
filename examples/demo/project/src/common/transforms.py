from pyspark.sql import DataFrame, functions as F


def clean_orders(raw: DataFrame) -> DataFrame:
    """Standardize raw orders into the silver shape."""
    return raw.select(
        "order_id",
        "cust_id",
        F.to_date("order_ts").alias("order_date"),
        (F.col("amount") - F.col("discount")).cast("decimal(12,2)").alias("net_amount"),
        "status",
        "is_test",
    )

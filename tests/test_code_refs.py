from pathlib import Path

from genie_review.code_refs import executed_files, python_refs, qualify, select_output_columns, sql_refs

PARAMS = {"catalog": "main"}


def test_sql_reads_writes_columns_and_join_types():
    sql = """
CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.gold.segment_revenue') AS
SELECT c.segment, SUM(o.net_amount) AS net_revenue
FROM IDENTIFIER(:catalog || '.silver.orders') o
JOIN IDENTIFIER(:catalog || '.bronze.customers') c
  ON o.customer_id = c.cust_id
GROUP BY c.segment;
"""
    r = sql_refs(sql, PARAMS)
    assert set(r.writes) == {"main.gold.segment_revenue"}
    assert set(r.reads) == {"main.silver.orders", "main.bronze.customers"}
    assert "segment" in r.columns["main.bronze.customers"]
    assert r.outputs["main.gold.segment_revenue"] == ["segment", "net_revenue"]
    assert r.joins == [{"left": ["main.silver.orders", "customer_id"], "right": ["main.bronze.customers", "cust_id"], "line": 6}]


def test_sql_unqualified_names_use_pipeline_defaults():
    r = sql_refs("CREATE MATERIALIZED VIEW daily AS SELECT * FROM raw_orders", {"__default_catalog": "c", "__default_schema": "s"})
    assert r.writes.keys() == {"c.s.daily"} and r.reads.keys() == {"c.s.raw_orders"}


def test_sql_skips_unresolvable_names():
    r = sql_refs("SELECT * FROM some_table", {})
    assert not r.reads


def test_python_fstrings_resolve_through_args_and_aliases():
    code = '''
import argparse
args = argparse.ArgumentParser().parse_args()
cat = args.catalog
orders = spark.table(f"{cat}.silver.orders")
customers = spark.table(f"{args.catalog}.bronze.customers")
top = orders.join(customers, orders.cust_id == customers.cust_id).select(customers.email)
top.write.mode("overwrite").saveAsTable(f"{cat}.gold.contacts")
spark.table(f"{unknown}.x.y")
'''
    r = python_refs(code, PARAMS)
    assert set(r.reads) == {"main.silver.orders", "main.bronze.customers"}
    assert set(r.writes) == {"main.gold.contacts"}
    assert "email" in r.columns["main.bronze.customers"]
    assert r.joins[0]["left"] == ["main.silver.orders", "cust_id"]
    assert r.unresolved and r.unresolved[0]["line"] == 9


def test_select_output_columns_reads_aliases():
    code = 'df.select("order_id", F.col("cust_id").alias("customer_id"), F.to_date("ts").alias("order_date"))'
    assert select_output_columns(code) == ["order_id", "customer_id", "order_date"]


def test_qualify():
    assert qualify("a.b.c", {}) == "a.b.c"
    assert qualify("b.c", {"__default_catalog": "a"}) == "a.b.c"
    assert qualify("c", {}) is None


def test_executed_files_follows_local_imports(tmp_path: Path):
    (tmp_path / "src/common").mkdir(parents=True)
    (tmp_path / "src/common/__init__.py").write_text("")
    (tmp_path / "src/common/transforms.py").write_text("from .dates import d\n")
    (tmp_path / "src/common/dates.py").write_text("d = 1\n")
    (tmp_path / "src/task.py").write_text("import os\nfrom common.transforms import clean\n")
    assert executed_files(tmp_path, "src/task.py") == {
        "src/task.py", "src/common/transforms.py", "src/common/dates.py"}


def test_sql_identifier_with_multiple_params():
    r = sql_refs("SELECT * FROM IDENTIFIER(:catalog || '.' || :schema || '.orders')", {"catalog": "main", "schema": "sales"})
    assert set(r.reads) == {"main.sales.orders"}


def test_python_notebook_widget_parameters():
    code = 'catalog = dbutils.widgets.get("catalog")\nspark.table("samples.nyctaxi.trips").write.saveAsTable(f"{catalog}.default.daily")\n'
    r = python_refs(code, {"catalog": "main"})
    assert set(r.reads) == {"samples.nyctaxi.trips"} and set(r.writes) == {"main.default.daily"}


def test_output_columns_from_groupby_agg():
    code = 'df.groupBy(F.to_date("ts").alias("pickup_date")).agg(F.count("*").alias("trips")).withColumn("load_ts", F.now())'
    assert set(select_output_columns(code)) == {"load_ts", "pickup_date", "trips"}


def test_sql_aggregates_and_status_filters():
    sql = """CREATE TABLE IDENTIFIER(:catalog || '.s.out') AS
SELECT c.seg, SUM(o.o_totalprice) AS order_revenue, COUNT(DISTINCT o.o_orderkey) AS fulfilled_orders
FROM samples.tpch.orders o JOIN samples.tpch.lineitem l ON o.o_orderkey = l.l_orderkey
JOIN samples.tpch.customer c ON c.c_custkey = o.o_custkey
WHERE l.l_linestatus = 'F' GROUP BY c.seg"""
    r = sql_refs(sql, {"catalog": "main"})
    agg = r.aggregates[0]
    assert (agg["table"], agg["column"], agg["func"]) == ("samples.tpch.orders", "o_totalprice", "SUM")
    assert [["samples.tpch.orders", "o_orderkey"], ["samples.tpch.lineitem", "l_orderkey"]] in agg["joins"]
    f = r.filters[0]
    assert (f["table"], f["column"], f["value"]) == ("samples.tpch.lineitem", "l_linestatus", "F")
    assert "fulfilled_orders" in f["outputs"]

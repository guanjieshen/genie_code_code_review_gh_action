-- Net revenue by customer market segment (sales dashboard)
CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :schema || '.tpch_segment_revenue') AS
SELECT
  c.c_mktsegment AS segment,
  SUM(l.l_extendedprice * (1 - l.l_discount)) AS net_revenue
FROM samples.tpch.customer c
JOIN samples.tpch.orders o ON c.c_custkey = o.o_custkey
JOIN samples.tpch.lineitem l ON o.o_orderkey = l.l_orderkey
GROUP BY c.c_mktsegment;

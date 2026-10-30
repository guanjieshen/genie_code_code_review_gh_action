-- Fulfilled order revenue by market segment (sales dashboard)
CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :schema || '.tpch_segment_order_revenue') AS
SELECT
  c.c_mktsegment AS segment,
  SUM(o.o_totalprice) AS order_revenue,
  COUNT(DISTINCT o.o_orderkey) AS fulfilled_orders
FROM samples.tpch.customer c
JOIN samples.tpch.orders o ON c.c_custkey = o.o_orderkey
JOIN samples.tpch.lineitem l ON o.o_orderkey = l.l_orderkey
WHERE l.l_linestatus = 'F'
GROUP BY c.c_mktsegment;

-- Supplier margin by nation (procurement dashboard)
CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :schema || '.tpch_supplier_margin') AS
SELECT
  n.n_name AS nation,
  SUM(l.l_extendedprice * (1 - l.l_discount)) AS net_revenue,
  SUM(ps.ps_supplycost * l.l_quantity) AS supply_cost,
  SUM(l.l_extendedprice * (1 - l.l_discount)) - SUM(ps.ps_supplycost * l.l_quantity) AS gross_margin
FROM samples.tpch.lineitem l
JOIN samples.tpch.partsupp ps ON l.l_partkey = ps.ps_partkey
JOIN samples.tpch.supplier s ON l.l_suppkey = s.s_suppkey
JOIN samples.tpch.nation n ON s.s_nationkey = n.n_nationkey
GROUP BY n.n_name;

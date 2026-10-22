CREATE SCHEMA IF NOT EXISTS {{catalog}}.{{schema}};

CREATE OR REPLACE TABLE {{catalog}}.{{schema}}.bronze_raw_orders (
  order_id BIGINT, cust_id BIGINT, order_ts TIMESTAMP, amount DECIMAL(12,2), discount DECIMAL(12,2),
  status STRING COMMENT 'PLACED | SHIPPED | CANCELLED',
  is_test BOOLEAN COMMENT 'Synthetic test orders; excluded from all revenue metrics'
) COMMENT 'Raw orders landed from the order service';

INSERT INTO {{catalog}}.{{schema}}.bronze_raw_orders
SELECT id, id % 500, timestamp'2026-09-01' + make_interval(0, 0, 0, 0, 0, id % 43200, 0),
       cast(10 + (id % 90) AS decimal(12,2)), cast(id % 5 AS decimal(12,2)),
       element_at(array('PLACED', 'SHIPPED', 'CANCELLED'), cast(id % 3 + 1 AS int)), id % 50 = 0
FROM range(1, 20001) t(id);

CREATE OR REPLACE TABLE {{catalog}}.{{schema}}.bronze_customers (
  cust_id STRING COMMENT 'Customer id as string (legacy CRM format)', email STRING, full_name STRING,
  segment STRING COMMENT 'SMB | MID | ENT', is_free_trial BOOLEAN
) COMMENT 'Customer master from CRM';

INSERT INTO {{catalog}}.{{schema}}.bronze_customers
SELECT cast(id AS string), concat('user', id, '@example.com'), concat('Customer ', id),
       element_at(array('SMB', 'MID', 'ENT'), cast(id % 3 + 1 AS int)), id % 10 = 0
FROM range(0, 500) t(id);

CREATE OR REPLACE TABLE {{catalog}}.{{schema}}.silver_orders (
  order_id BIGINT NOT NULL, cust_id BIGINT COMMENT 'FK to bronze_customers.cust_id', order_date DATE,
  net_amount DECIMAL(12,2), status STRING, is_test BOOLEAN
) CLUSTER BY (order_date) COMMENT 'Cleaned orders. Written by the orders_etl silver task.';

INSERT INTO {{catalog}}.{{schema}}.silver_orders
SELECT order_id, cust_id, to_date(order_ts), amount - discount, status, is_test
FROM {{catalog}}.{{schema}}.bronze_raw_orders;

CREATE OR REPLACE TABLE {{catalog}}.{{schema}}.gold_revenue_daily
COMMENT 'AUTHORITATIVE daily net revenue. Net revenue = SUM(net_amount) excluding status = CANCELLED and is_test = true. Owned by Finance.'
AS SELECT order_date, SUM(net_amount) AS net_revenue, COUNT(*) AS orders
FROM {{catalog}}.{{schema}}.silver_orders WHERE status <> 'CANCELLED' AND NOT is_test GROUP BY order_date;

CREATE OR REPLACE TABLE {{catalog}}.{{schema}}.gold_revenue_daily_v1
COMMENT 'DEPRECATED 2026-06: gross revenue including cancelled and test orders. Use gold_revenue_daily instead.'
AS SELECT order_date, SUM(net_amount) AS revenue FROM {{catalog}}.{{schema}}.silver_orders GROUP BY order_date;

CREATE OR REPLACE VIEW {{catalog}}.{{schema}}.gold_customer_ltv
COMMENT 'Customer lifetime value. Used by the Customer 360 dashboard.'
AS SELECT o.cust_id, c.segment, SUM(o.net_amount) AS ltv
FROM {{catalog}}.{{schema}}.silver_orders o
JOIN {{catalog}}.{{schema}}.bronze_customers c ON cast(o.cust_id AS STRING) = c.cust_id
WHERE o.status <> 'CANCELLED' AND NOT o.is_test GROUP BY o.cust_id, c.segment;

CREATE OR REPLACE TABLE {{catalog}}.{{schema}}.gold_segment_revenue
AS SELECT c.segment, SUM(o.net_amount) AS net_revenue
FROM {{catalog}}.{{schema}}.silver_orders o
JOIN {{catalog}}.{{schema}}.bronze_customers c ON cast(o.cust_id AS STRING) = c.cust_id
WHERE o.status <> 'CANCELLED' AND NOT o.is_test GROUP BY c.segment;

CREATE OR REPLACE VIEW {{catalog}}.{{schema}}.gold_revenue_metrics
WITH METRICS LANGUAGE YAML COMMENT 'Governed revenue metrics (Finance).' AS $$
version: 1.1
source: {{catalog}}.{{schema}}.silver_orders
filter: status <> 'CANCELLED' AND NOT is_test
dimensions:
  - name: order_date
    expr: order_date
measures:
  - name: net_revenue
    expr: SUM(net_amount)
    comment: Net revenue excludes cancelled and test orders.
$$;

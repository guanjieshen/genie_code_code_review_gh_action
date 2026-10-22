-- Revenue by customer segment, v2 (feeds the new Exec dashboard)
CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :schema || '.gold_segment_revenue_v2') AS
SELECT
  c.customer_segment AS segment,
  SUM(o.net_amount)  AS net_revenue,
  COUNT(DISTINCT i.sku) AS skus_sold
FROM IDENTIFIER(:catalog || '.' || :schema || '.silver_orders') o
JOIN IDENTIFIER(:catalog || '.' || :schema || '.bronze_customers') c
  ON o.customer_id = c.cust_id
LEFT JOIN IDENTIFIER(:catalog || '.' || :schema || '.silver_order_items') i
  ON o.order_id = i.order_id
GROUP BY c.customer_segment;

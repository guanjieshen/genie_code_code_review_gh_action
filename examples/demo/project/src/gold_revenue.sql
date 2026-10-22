-- Authoritative daily net revenue (Finance)
CREATE OR REPLACE TABLE IDENTIFIER(:catalog || '.' || :schema || '.gold_revenue_daily') AS
SELECT order_date, SUM(net_amount) AS net_revenue, COUNT(*) AS orders
FROM IDENTIFIER(:catalog || '.' || :schema || '.silver_orders')
WHERE status <> 'CANCELLED' AND NOT is_test
GROUP BY order_date;

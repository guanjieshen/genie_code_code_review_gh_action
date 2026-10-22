# What the demo pull request should find

`./demo/create_demo_pr.sh` opens a pull request that looks like a normal feature: a new segment revenue table, an exec KPI task, and a column rename in a shared helper. Each issue below needs a fact from the workspace to spot, so a reviewer that only reads the diff will miss most of them.

| # | Issue | Where | Check | Workspace fact used |
|---|---|---|---|---|
| 1 | `c.customer_segment` does not exist (the column is `segment`) | `src/gold_segment_revenue_v2.sql` | `contract.column-mismatch` | Table schema |
| 2 | Renaming `cust_id` breaks the `gold_customer_ltv` view | `src/common/transforms.py` | `lineage.downstream-break` | Column lineage |
| 3 | Overwriting `silver_orders` with a changed schema | `src/common/transforms.py` | `contract.write-schema` | Existing table schema |
| 4 | Join of `customer_id` (BIGINT) with `cust_id` (STRING) | `src/gold_segment_revenue_v2.sql` | `contract.type-mismatch` | Column types |
| 5 | Join of `ltv.cust_id` (BIGINT) with `customers.cust_id` (STRING) | `src/exec_kpis.py` | `contract.type-mismatch` | Column types |
| 6 | Net revenue computed without excluding cancelled and test orders | `src/gold_segment_revenue_v2.sql` | `semantic.metric-definition-drift` | Metric view `gold_revenue_metrics` |
| 7 | Reads deprecated `gold_revenue_daily_v1` | `src/exec_kpis.py` | `semantic.non-authoritative-source` | Table tags and comment |
| 8 | PII `email` copied into a new untagged table | `src/exec_kpis.py` | `governance.tagged-data-flow` | Column tags |
| 9 | Cluster on `11.3.x-scala2.12`, not offered by the workspace | `resources/orders_etl.job.yml` | `deploy.runtime-support` | Offered runtimes |
| 10 | Deploying overwrites the on-call edit to the job | `resources/orders_etl.job.yml` | `deploy.drift` | Deployed job settings |
| 11 | `silver_order_items` does not exist | `src/gold_segment_revenue_v2.sql` | `contract.unknown-object` | Table existence |

The review may also mention issues outside this list. A general code reviewer (for example GitHub Copilot) should catch the one plain logic bug in the pull request: `SUM(o.net_amount)` after joining order items counts each order once per item. Lakehouse Review intentionally does not report it, because it does not need workspace context.

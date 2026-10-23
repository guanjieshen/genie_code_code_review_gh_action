# Sample review

This is the comment Lakehouse Review posts for the [demo pull request](../examples/demo/README.md), rendered with the current version from the 12-check regression fixture (a real run). Catalog and schema names are replaced with `main.shop`.

---

# Lakehouse Review

Adds two tasks to the `orders_etl` job and a new job cluster.
Renames `silver_orders.cust_id` to `customer_id`.
Creates three Gold tables for executive and segment reporting.
Reads customer PII and deprecated gross-revenue data.
Impacts existing Finance views and jobs consuming `silver_orders`.

**Review status:** Blocking issues found  
**Issues:** 10 — 2 critical · 5 high · 3 medium (12 supporting checks)

| Review area | Issues | Highest |
|---|---:|---|
| Lineage, relationships & ontology | 1 | 🔴 Critical |
| Data contracts & compatibility | 4 | 🔴 Critical |
| Business semantics & metrics | 2 | 🟠 High |
| Deployment & operations | 2 | 🟠 High |
| Security & governance | 1 | 🟠 High |

## Needs attention

**🔴 Critical — Removing `cust_id` from `silver_orders` breaks `gold_customer_ltv`**

Column lineage shows `gold_customer_ltv.cust_id` reading `silver_orders.cust_id`.

**Impact:** `gold_customer_ltv` will fail or return wrong results once this deploys.

**Recommended fix:** Update `gold_customer_ltv` to read the new column, or keep `cust_id` until every reader is migrated.

[`src/common/transforms.py:8`](https://github.com/acme/data-platform/blob/0123abc/src/common/transforms.py#L8) · Exact check + Lineage + Unity Catalog · Confirmed

<details><summary>Technical evidence</summary>

- Supporting checks: `lineage.downstream-break`: Removing `cust_id` from `silver_orders` breaks `gold_customer_ltv`; `contract.write-schema`: Drops column `cust_id` from existing table `silver_orders`
- Rating: critical severity, confirmed confidence, blocker
- Lineage: column `silver_orders.cust_id`: 1 downstream column(s); table `silver_orders`: 6 downstream object(s) (2 jobs, 4 tables/views), not all of which read the changed column
- Objects: `uc:main.shop.silver_orders`
- Evidence: `ev:lineage:main.shop.silver_orders.cust_id:downstream`, `ev:lineage:main.shop.silver_orders:downstream`, `ev:uc:main.shop.silver_orders:metadata`
- Finding ids: `f-eb660dbf3e`, `f-8bf6ebb562`

</details>

**🔴 Critical — Reads missing table `silver_order_items`**

`main.shop.silver_order_items` does not exist in the target workspace, and no task in this PR creates it.

**Impact:** The task fails when it reads the table.

**Recommended fix:** Correct the table name, create `silver_order_items` before this task runs, or add the task that produces it.

[`src/gold_segment_revenue_v2.sql:10`](https://github.com/acme/data-platform/blob/0123abc/src/gold_segment_revenue_v2.sql#L10) · Exact check + Unity Catalog · Confirmed

<details><summary>Technical evidence</summary>

- Check: `contract.unknown-object`
- Rating: critical severity, confirmed confidence, blocker
- Objects: `uc:main.shop.silver_order_items`
- Evidence: `ev:uc:main.shop.silver_order_items:metadata`
- Finding ids: `f-e9f4018569`

</details>

**🟠 High — Executive revenue includes cancelled and test orders**

The governed net-revenue metric excludes cancelled and test orders, while this task copies a source explicitly documented as including both.

**Recommended fix:** Use the governed revenue source or apply its required filters and net-revenue definition.

[`src/exec_kpis.py:13`](https://github.com/acme/data-platform/blob/0123abc/src/exec_kpis.py#L13) · Genie Code + Unity Catalog · High confidence

<details><summary>Technical evidence</summary>

- Supporting checks: `semantic.metric-definition-drift`: Executive revenue includes cancelled and test orders; `semantic.non-authoritative-source`: Executive trend uses a deprecated revenue source
- Rating: high severity, high confidence, action required
- Objects: `uc:main.shop.gold_revenue_daily`, `uc:main.shop.gold_revenue_daily_v1`, `uc:main.shop.gold_revenue_metrics`
- Evidence: `ev:uc:main.shop.gold_revenue_daily:tags`, `ev:uc:main.shop.gold_revenue_daily_v1:metadata`, `ev:uc:main.shop.gold_revenue_daily_v1:tags`, `ev:uc:main.shop.gold_revenue_metrics:metric_view`
- Finding ids: `f-6a026a6423`, `f-4b8936b0ae`

</details>

**Also needs action (3)**

- 🟠 **High** — Segment revenue does not apply governed revenue filters · [`src/gold_segment_revenue_v2.sql:5`](https://github.com/acme/data-platform/blob/0123abc/src/gold_segment_revenue_v2.sql#L5) · details under *Business semantics & metrics*
- 🟠 **High** — References missing column `customer_segment` on `bronze_customers` · [`src/gold_segment_revenue_v2.sql:4`](https://github.com/acme/data-platform/blob/0123abc/src/gold_segment_revenue_v2.sql#L4) · details under *Data contracts & compatibility*
- 🟠 **High** — Uses runtime `11.3.x-scala2.12`, which this workspace does not offer · [`resources/orders_etl.job.yml:41`](https://github.com/acme/data-platform/blob/0123abc/resources/orders_etl.job.yml#L41) · details under *Deployment & operations*

## Review details

<details><summary><b>Lineage, relationships & ontology</b> — 1 issue · Critical</summary>

- 🔴 Removing `cust_id` from `silver_orders` breaks `gold_customer_ltv` (expanded in **Needs attention**)

</details>

<details><summary><b>Data contracts & compatibility</b> — 4 issues · Critical</summary>

- 🔴 Reads missing table `silver_order_items` (expanded in **Needs attention**)

**🟠 High — References missing column `customer_segment` on `bronze_customers`**

`bronze_customers` has no column `customer_segment`. The closest existing column is `segment`.

**Impact:** The query fails with an unresolved column error.

**Recommended fix:** Use `segment`, or add `customer_segment` to `bronze_customers`.

[`src/gold_segment_revenue_v2.sql:4`](https://github.com/acme/data-platform/blob/0123abc/src/gold_segment_revenue_v2.sql#L4) · Exact check + Unity Catalog · Confirmed

<details><summary>Technical evidence</summary>

- Check: `contract.column-mismatch`
- Rating: high severity, confirmed confidence, action required
- Objects: `uc:main.shop.bronze_customers`
- Evidence: `ev:uc:main.shop.bronze_customers:metadata`
- Finding ids: `f-0c3fc9a4c1`

</details>

**🟡 Medium — Joins `cust_id` (bigint) to `cust_id` (string)**

`gold_customer_ltv.cust_id` is bigint and `bronze_customers.cust_id` is string, so Spark casts one side implicitly.

**Impact:** If any `bronze_customers.cust_id` value is not numeric, it becomes NULL and those rows drop out of the join.

**To validate:** Check whether every `bronze_customers.cust_id` value is numeric. If so, no rows are lost; the explicit cast is still recommended.

**Recommended fix:** Cast both sides to one type explicitly, or align the column types at the source.

[`src/exec_kpis.py:20`](https://github.com/acme/data-platform/blob/0123abc/src/exec_kpis.py#L20) · Exact check + Unity Catalog · High confidence

<details><summary>Technical evidence</summary>

- Check: `contract.type-mismatch`
- Rating: medium severity, high confidence, review recommended
- Objects: `uc:main.shop.bronze_customers`, `uc:main.shop.gold_customer_ltv`
- Evidence: `ev:uc:main.shop.bronze_customers:metadata`, `ev:uc:main.shop.gold_customer_ltv:metadata`
- Finding ids: `f-3c5cf80e77`

</details>

**🟡 Medium — Joins `customer_id` (bigint) to `cust_id` (string)**

`silver_orders.customer_id` is bigint and `bronze_customers.cust_id` is string, so Spark casts one side implicitly.

**Impact:** If any `bronze_customers.cust_id` value is not numeric, it becomes NULL and those rows drop out of the join.

**To validate:** Check whether every `bronze_customers.cust_id` value is numeric. If so, no rows are lost; the explicit cast is still recommended.

**Recommended fix:** Cast both sides to one type explicitly, or align the column types at the source.

[`src/gold_segment_revenue_v2.sql:9`](https://github.com/acme/data-platform/blob/0123abc/src/gold_segment_revenue_v2.sql#L9) · Exact check + Unity Catalog · High confidence

<details><summary>Technical evidence</summary>

- Check: `contract.type-mismatch`
- Rating: medium severity, high confidence, review recommended
- Objects: `uc:main.shop.bronze_customers`, `uc:main.shop.silver_orders`
- Evidence: `ev:uc:main.shop.bronze_customers:metadata`, `ev:uc:main.shop.silver_orders:metadata`
- Finding ids: `f-de95d58779`

</details>

</details>

<details><summary><b>Business semantics & metrics</b> — 2 issues · High</summary>

- 🟠 Executive revenue includes cancelled and test orders (expanded in **Needs attention**)

**🟠 High — Segment revenue does not apply governed revenue filters**

The query sums `net_amount` without excluding `CANCELLED` and test orders, unlike the governed Finance metric. Its item join can also multiply an order's revenue across SKUs.

**Recommended fix:** Filter cancelled/test orders and aggregate revenue before joining item-level data.

[`src/gold_segment_revenue_v2.sql:5`](https://github.com/acme/data-platform/blob/0123abc/src/gold_segment_revenue_v2.sql#L5) · Genie Code + Unity Catalog · High confidence

<details><summary>Technical evidence</summary>

- Check: `semantic.metric-definition-drift`
- Rating: high severity, high confidence, action required
- Objects: `uc:main.shop.gold_revenue_metrics`
- Evidence: `ev:uc:main.shop.gold_revenue_metrics:metric_view`
- Finding ids: `f-8c34ff0942`

</details>

</details>

<details><summary><b>Deployment & operations</b> — 2 issues · High</summary>

**🟠 High — Uses runtime `11.3.x-scala2.12`, which this workspace does not offer**

The target workspace does not list `11.3.x-scala2.12` as an available Databricks Runtime.

**Impact:** The job cannot start a cluster with this runtime.

**Recommended fix:** Set `spark_version` to a runtime the workspace offers, for example `18.2.x-scala2.13`.

[`resources/orders_etl.job.yml:41`](https://github.com/acme/data-platform/blob/0123abc/resources/orders_etl.job.yml#L41) · Exact check + Workspace · Confirmed

<details><summary>Technical evidence</summary>

- Check: `deploy.runtime-support`
- Rating: high severity, confirmed confidence, action required
- Objects: `runtime:11.3.x-scala2.12`
- Evidence: `ev:workspace:spark_version:11.3.x-scala2.12`
- Finding ids: `f-dc75750e12`

</details>

**🟡 Medium — Deploy reverts changes made outside the bundle to job `orders_etl`**

The deployed job differs from the bundle on the base branch in `tags`, `timeout_seconds`.

**Impact:** Deploying this PR replaces those settings with the bundle's values.

**Recommended fix:** Add the intended settings to the bundle, or confirm with the job owner that they should be reverted.

[`resources/orders_etl.job.yml:3`](https://github.com/acme/data-platform/blob/0123abc/resources/orders_etl.job.yml#L3) · Exact check + Deployed job · Confirmed

<details><summary>Technical evidence</summary>

- Check: `deploy.drift`
- Rating: medium severity, confirmed confidence, review recommended
- Objects: `job:906740025351921`
- Evidence: `ev:job:906740025351921:drift`
- Finding ids: `f-cc9eae635d`

</details>

</details>

<details><summary><b>Security & governance</b> — 1 issue · High</summary>

**🟠 High — PII email is copied into an untagged Gold table**

`bronze_customers.email` carries the `data_classification=pii` tag, but the newly created `gold_top_customer_contacts` table has no corresponding tag or masking configuration.

**To validate:** Check the target's grants, column masks, and row filters. This review saw classification tags only, not effective access.

**Recommended fix:** Apply equivalent PII tags and masking/access controls to the target before writing customer emails.

[`src/exec_kpis.py:23`](https://github.com/acme/data-platform/blob/0123abc/src/exec_kpis.py#L23) · Genie Code + Unity Catalog · Needs validation

<details><summary>Technical evidence</summary>

- Check: `governance.tagged-data-flow`
- Rating: high severity, medium confidence, review recommended
- Objects: `uc:main.shop.bronze_customers`, `uc:main.shop.gold_top_customer_contacts`
- Evidence: `ev:uc:main.shop.bronze_customers:tags`, `ev:uc:main.shop.gold_top_customer_contacts:metadata`
- Finding ids: `f-6662d3d54a`

</details>

</details>

<details><summary>Analysis coverage</summary>

| Capability | Status | Scope | Findings |
|---|---|---|---:|
| Bundle and code analysis | Ran | 4 changed file(s), 3 affected task(s) | 8 |
| Unity Catalog metadata | Ran | 9 table(s) checked | 9 |
| Lineage | Ran | 1 table(s), 1 changed column(s) | 1 |
| Governance tags and metric views | Ran | 3 tagged object(s), 1 metric view(s) | 4 |
| Deployed job state | Ran | 1 deployed job(s) compared | 1 |
| Workspace runtimes | Ran | 1 runtime version(s) checked | 1 |
| Genie Code review | Ran | AI review of the planned checks | 4 |
| Genie One (ontology) | Not needed | Unity Catalog evidence answered the semantic checks | 0 |

<sub>A finding counts toward every capability it used.</sub>

</details>

<sub>AI review engine: Genie Code `gpt-5.6-sol` via target workspace. Genie One: not called.</sub>


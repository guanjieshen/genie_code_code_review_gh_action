# Sample review

This is the comment Lakehouse Review posts for the [demo pull request](../examples/demo/README.md) (12 findings from a real run, rendered with the current version). Catalog and schema names are replaced with `main.shop`.

---

# Lakehouse Review

Adds two tasks to the `orders_etl` job and a new job cluster.
Renames `silver_orders.cust_id` to `customer_id`.
Creates three Gold tables for executive and segment reporting.
Reads customer PII and deprecated gross-revenue data.
Impacts existing Finance views and jobs consuming `silver_orders`.

**Review status:** Blocking issues found  
**Findings:** 2 critical · 7 high · 3 medium (10 issues from 12 checks)

| Review area | Findings | Priority |
|---|---:|---|
| Lineage, relationships & ontology | 2 | 🔴 Critical |
| Data contracts & compatibility | 4 | 🔴 Critical |
| Business semantics & metrics | 3 | 🟠 High |
| Deployment & operations | 2 | 🟠 High |
| Security & governance | 1 | 🟠 High |

## Needs attention

**🔴 Critical — Removing `cust_id` breaks 1 downstream column(s)**

Column lineage shows these consumers of `main.shop.silver_orders.cust_id`: main.shop.gold_customer_ltv.cust_id.

**Impact:** Downstream tables and views that read the removed column fail or return wrong results after deploy.

**Recommended fix:** Update or migrate the downstream objects before (or with) this change.

[`src/common/transforms.py:8`](https://github.com/acme/data-platform/blob/0123abc/src/common/transforms.py#L8) · Lineage + Unity Catalog · Confirmed

Also flagged: Write changes the schema of existing table `silver_orders` (`contract.write-schema`)

<details><summary>Technical evidence</summary>

- Checks: `contract.write-schema`, `lineage.downstream-break`
- Merge impact: blocker (critical, confirmed confidence, 7 downstream object(s))
- Objects: `uc:main.shop.silver_orders`
- Evidence: `ev:lineage:main.shop.silver_orders.cust_id:downstream`, `ev:lineage:main.shop.silver_orders:downstream`, `ev:uc:main.shop.silver_orders:metadata`
- Finding ids: `f-9adff2af9e`, `f-a623ff5741`

</details>

---

**🔴 Critical — `main.shop.silver_order_items` does not exist**

The code reads `main.shop.silver_order_items`, which does not exist in the target workspace and is not created by this PR.

**Impact:** The task fails at runtime when it reads the missing object.

**Recommended fix:** Create the table first, fix the name, or add the task that produces it.

[`src/gold_segment_revenue_v2.sql:10`](https://github.com/acme/data-platform/blob/0123abc/src/gold_segment_revenue_v2.sql#L10) · Unity Catalog · Confirmed

<details><summary>Technical evidence</summary>

- Checks: `contract.unknown-object`
- Merge impact: blocker (critical, confirmed confidence)
- Objects: `uc:main.shop.silver_order_items`
- Evidence: `ev:uc:main.shop.silver_order_items:metadata`
- Finding ids: `f-d9f6c20aed`

</details>

---

**🟠 High — Executive revenue includes cancelled and test orders**

The governed net-revenue metric excludes cancelled and test orders, while this task copies a source explicitly documented as including both.

**Recommended fix:** Use the governed revenue source or apply its required filters and net-revenue definition.

[`src/exec_kpis.py:13`](https://github.com/acme/data-platform/blob/0123abc/src/exec_kpis.py#L13) · Genie + Unity Catalog · High confidence

Also flagged: Executive trend uses a deprecated revenue source (`semantic.non-authoritative-source`)

<details><summary>Technical evidence</summary>

- Checks: `semantic.metric-definition-drift`, `semantic.non-authoritative-source`
- Merge impact: action required (high, high confidence, governed data product)
- Objects: `uc:main.shop.gold_revenue_daily`, `uc:main.shop.gold_revenue_daily_v1`, `uc:main.shop.gold_revenue_metrics`
- Evidence: `ev:uc:main.shop.gold_revenue_daily:tags`, `ev:uc:main.shop.gold_revenue_daily_v1:metadata`, `ev:uc:main.shop.gold_revenue_daily_v1:tags`, `ev:uc:main.shop.gold_revenue_metrics:metric_view`
- Finding ids: `f-6a026a6423`, `f-4b8936b0ae`

</details>

3 more issue(s) need action. See the review areas below.

## Review details

<details><summary><b>Lineage, relationships & ontology</b> — 2 findings · Critical</summary>

- 🔴 Removing `cust_id` breaks 1 downstream column(s) (shown in **Needs attention**)

</details>

<details><summary><b>Data contracts & compatibility</b> — 4 findings · Critical</summary>

- 🔴 `main.shop.silver_order_items` does not exist (shown in **Needs attention**)

**🟠 High — `bronze_customers.customer_segment` does not exist**

`main.shop.bronze_customers` has no column `customer_segment`. Live columns: cust_id, email, full_name, is_free_trial, segment.

**Impact:** The query fails at runtime with an unresolved column error.

[`src/gold_segment_revenue_v2.sql:4`](https://github.com/acme/data-platform/blob/0123abc/src/gold_segment_revenue_v2.sql#L4) · Unity Catalog · Confirmed

<details><summary>Technical evidence</summary>

- Checks: `contract.column-mismatch`
- Merge impact: action required (high, confirmed confidence)
- Objects: `uc:main.shop.bronze_customers`
- Evidence: `ev:uc:main.shop.bronze_customers:metadata`
- Finding ids: `f-a3f30a5ca6`

</details>

**🟡 Medium — Join compares bigint `cust_id` with string `cust_id`**

`main.shop.gold_customer_ltv.cust_id` is bigint and `main.shop.bronze_customers.cust_id` is string. The join relies on an implicit cast, which is slow and can silently drop or mismatch rows.

**Impact:** Spark casts one side implicitly; values that do not convert become NULL and do not match, and the join cannot use data skipping.

**Recommended fix:** Cast explicitly to a common type, or align the column types at the source.

[`src/exec_kpis.py:20`](https://github.com/acme/data-platform/blob/0123abc/src/exec_kpis.py#L20) · Unity Catalog · High confidence

<details><summary>Technical evidence</summary>

- Checks: `contract.type-mismatch`
- Merge impact: review recommended (medium, high confidence)
- Objects: `uc:main.shop.bronze_customers`, `uc:main.shop.gold_customer_ltv`
- Evidence: `ev:uc:main.shop.bronze_customers:metadata`, `ev:uc:main.shop.gold_customer_ltv:metadata`
- Finding ids: `f-8eb470a7d9`

</details>

**🟡 Medium — Join compares bigint `customer_id` with string `cust_id`**

`main.shop.silver_orders.customer_id` is bigint and `main.shop.bronze_customers.cust_id` is string. The join relies on an implicit cast, which is slow and can silently drop or mismatch rows.

**Impact:** Spark casts one side implicitly; values that do not convert become NULL and do not match, and the join cannot use data skipping.

**Recommended fix:** Cast explicitly to a common type, or align the column types at the source.

[`src/gold_segment_revenue_v2.sql:9`](https://github.com/acme/data-platform/blob/0123abc/src/gold_segment_revenue_v2.sql#L9) · Unity Catalog · High confidence

<details><summary>Technical evidence</summary>

- Checks: `contract.type-mismatch`
- Merge impact: review recommended (medium, high confidence)
- Objects: `uc:main.shop.bronze_customers`, `uc:main.shop.silver_orders`
- Evidence: `ev:uc:main.shop.bronze_customers:metadata`, `ev:uc:main.shop.silver_orders:metadata`
- Finding ids: `f-e4b0ed446c`

</details>

</details>

<details><summary><b>Business semantics & metrics</b> — 3 findings · High</summary>

- 🟠 Executive revenue includes cancelled and test orders (shown in **Needs attention**)

**🟠 High — Segment revenue does not apply governed revenue filters**

The query sums `net_amount` without excluding `CANCELLED` and test orders, unlike the governed Finance metric. Its item join can also multiply an order's revenue across SKUs.

**Recommended fix:** Filter cancelled/test orders and aggregate revenue before joining item-level data.

[`src/gold_segment_revenue_v2.sql:5`](https://github.com/acme/data-platform/blob/0123abc/src/gold_segment_revenue_v2.sql#L5) · Genie + Unity Catalog · High confidence

<details><summary>Technical evidence</summary>

- Checks: `semantic.metric-definition-drift`
- Merge impact: action required (high, high confidence, governed data product)
- Objects: `uc:main.shop.gold_revenue_metrics`
- Evidence: `ev:uc:main.shop.gold_revenue_metrics:metric_view`
- Finding ids: `f-8c34ff0942`

</details>

</details>

<details><summary><b>Deployment & operations</b> — 2 findings · High</summary>

**🟠 High — Runtime `11.3.x-scala2.12` is not offered by this workspace**

The workspace does not offer `11.3.x-scala2.12`. Examples it offers: 15.4.x-scala2.12, 16.4.x-scala2.13, 16.4.x-scala2.12, 17.3.x-scala2.13, 18.2.x-scala2.13.

**Impact:** The job cannot start a cluster with this runtime in the target workspace.

**Recommended fix:** Use a supported LTS runtime.

[`resources/orders_etl.job.yml:41`](https://github.com/acme/data-platform/blob/0123abc/resources/orders_etl.job.yml#L41) · Workspace · Confirmed

<details><summary>Technical evidence</summary>

- Checks: `deploy.runtime-support`
- Merge impact: action required (high, confirmed confidence)
- Objects: `runtime:11.3.x-scala2.12`
- Evidence: `ev:workspace:spark_version:11.3.x-scala2.12`
- Finding ids: `f-b6ad1323a0`

</details>

**🟡 Medium — Deploy will overwrite UI changes on job `orders_etl`**

The deployed job differs from the bundle on the base branch in: timeout_seconds, tags. Deploying this PR replaces those settings.

**Impact:** Deploying this change silently reverts settings someone changed on the live job.

**Recommended fix:** Bring the out-of-band changes into the bundle, or confirm they should be reverted.

[`resources/orders_etl.job.yml:3`](https://github.com/acme/data-platform/blob/0123abc/resources/orders_etl.job.yml#L3) · Deployed job · Confirmed

<details><summary>Technical evidence</summary>

- Checks: `deploy.drift`
- Merge impact: review recommended (medium, confirmed confidence)
- Objects: `job:906740025351921`
- Evidence: `ev:job:906740025351921:drift`
- Finding ids: `f-8c5a08b893`

</details>

</details>

<details><summary><b>Security & governance</b> — 1 finding · High</summary>

**🟠 High — PII email is copied into an untagged Gold table**

`bronze_customers.email` carries the `data_classification=pii` tag, but the newly created `gold_top_customer_contacts` table has no corresponding tag or masking configuration.

**Recommended fix:** Apply equivalent PII tags and masking/access controls to the target before writing customer emails.

[`src/exec_kpis.py:23`](https://github.com/acme/data-platform/blob/0123abc/src/exec_kpis.py#L23) · Genie + Unity Catalog · Needs validation

<details><summary>Technical evidence</summary>

- Checks: `governance.tagged-data-flow`
- Merge impact: review recommended (high, medium confidence)
- Objects: `uc:main.shop.bronze_customers`, `uc:main.shop.gold_top_customer_contacts`
- Evidence: `ev:uc:main.shop.bronze_customers:tags`, `ev:uc:main.shop.gold_top_customer_contacts:metadata`
- Finding ids: `f-6662d3d54a`

</details>

</details>

<sub>Reviewer: Genie Code `gpt-5.6-sol` via target workspace.</sub>


# Architecture

```
pull_request
   │
   ▼
1. Impact analysis      resolve bundle (base + head), import graph, SQL/PySpark parsing
   │                    → impact graph: files → tasks → tables → columns
   ▼
2. Evidence             read-only workspace lookups: metadata, tags, metric views,
   │                    lineage, deployed job settings, offered runtimes
   ▼
3. Plan + exact checks  decide which checks apply; run the ones that need no model
   │
   ▼
4. Genie review         route check → Genie Code (read-only sandbox) with plan + evidence;
   │                    Genie One MCP for business definitions
   ▼
5. Publish              validate, require evidence, merge, rank → one PR comment
```

### Impact analysis

The core question is what in Databricks a pull request touches. The analyzer runs on both the base tree and the head tree and compares the results:

- **Files to resources.** `databricks bundle validate -o json` gives the fully resolved bundle for the target. Each task's entry file is mapped back to the repo, and local Python imports are followed, so a change to a shared helper reaches every task that imports it.
- **Code to data.** SQL is parsed with sqlglot (Databricks dialect). PySpark is parsed with Python's `ast`: `spark.table`, `saveAsTable`, `insertInto`, f-string table names resolved from task parameters, column references, and join conditions.
- **Changes.** Output columns are compared between base and head to find removed, added, and renamed columns. Resource configs are compared to find new tasks and compute changes.
- **Deployed state.** `databricks bundle summary` links bundle resources to deployed job ids.

Every edge records how it was found (`bundle:entry_file`, `import_graph`, `sql:sqlglot`, `python:ast`). Names that cannot be resolved statically are listed in the coverage section.

### Evidence ids

Each fact gets an id, such as `ev:uc:main.sales.orders:metadata` or `ev:lineage:main.sales.orders.cust_id:downstream`. Findings must cite these ids, or a Genie One question that was actually asked. Findings without valid evidence are dropped before publishing.

### Model routes

All model work goes through Genie Code (and Genie One for semantics). Before each review, a short Genie Code call checks the route: the target workspace first, then `genie-host` if set. If neither answers, the exact checks still post.

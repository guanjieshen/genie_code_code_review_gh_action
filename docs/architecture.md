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
5. Process results      normalize (area, confidence, merge impact) → correlate → prioritize
   │
   ▼
6. Report + publish     review.json + Markdown → one PR comment (updated in place) + job summary
```

| Stage | Module |
|---|---|
| Change discovery | `impact.py`, `bundle.py`, `code_refs.py` |
| Evidence collection | `evidence.py`, `access.py` |
| Detection | `checks.py` (exact checks), `genie.py` + `prompt.py` (Genie) |
| Validation | `publish.genie_findings` (schema, plan, evidence grounding, sanitizing) |
| Normalization | `normalize.py` (review area, evidence-based confidence, merge-impact policy, stable ids) |
| Correlation and prioritization | `correlate.py` (root-cause grouping, deterministic ordering, "Needs attention") |
| Rendering | `render.py` (Markdown and `review.json`) |
| Publishing | `publish.py` (PR comment, job summary, outputs, check status) |

### Confidence and grouping rules

- Exact checks backed by verified metadata, column lineage, deployed state, or workspace APIs are **confirmed**. Type mismatches are **high**: the types are verified, but whether rows are lost depends on the data.
- Genie semantic findings anchored to a metric view or a certified or deprecated dataset are **high**; otherwise **medium**. Governance findings are **medium** unless permissions and masking evidence exist. Genie's own score can only lower confidence.
- Findings that cite only unreadable metadata are **low** and hidden from the comment.
- Grouping uses two rules: a schema change on an existing table and the downstream break it causes (same table, same file), and semantic findings about the same deprecated dataset in the same file. Every other finding is its own issue.

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

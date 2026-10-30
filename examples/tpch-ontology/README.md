# TPC-H ontology demo

A pull request with four errors that **pass every type check** and contradict documented business rules. It shows what `analysis-depth: detailed` adds: Lakehouse Review asks Genie One what your Pages say about the joins, grains, and codes the change uses.

The demo reads the built-in `samples.tpch` schema, so it needs no data setup. The business rules are in [`pages/`](pages/), to recreate as Unity Catalog Pages in your workspace.

> **New to Lakehouse Review?** Set up the reviewer first with [the setup guide](../../docs/setup.md).

## Steps

| Step | What to do |
| --- | --- |
| 1 | Copy this folder to the root of a repository that already runs Lakehouse Review, or to a new one. |
| 2 | Set `catalog`, `schema`, and `warehouse_id` in `databricks.yml`. Use a **dedicated schema**: the review checks output tables against the definitions governed in their schema, so a schema shared with another domain's metrics can produce findings about those. |
| 3 | In the workspace, create three Pages from [`pages/`](pages/): **TPC-H Sample Schema**, **Order**, and **Line Item**. Paste each file's content. |
| 4 | Share the three Pages with the reviewer service principal. |
| 5 | Set `analysis-depth: detailed` in the Lakehouse Review workflow, commit, and push to the default branch. |
| 6 | Run `./create_demo_pr.sh` to open the pull request. |

The review posts as a comment within a few minutes. Compare it with [EXPECTED.md](EXPECTED.md).

> **Tip:** If a finding is low confidence, the reviewer probably cannot open the Page that defines the rule. Check the Page's sharing settings, then push again.

## What the files are

| Path | Purpose |
| --- | --- |
| `databricks.yml`, `resources/`, `src/` | The bundle on the default branch: one task that computes net revenue by segment correctly. |
| `pr/` | The files the pull request adds or changes: two new tasks with the planted errors. |
| `pages/` | The Page content that documents the TPC-H relationships, grains, and codes. |
| `create_demo_pr.sh` | Creates a branch with `pr/` applied and opens the pull request. |
| `EXPECTED.md` | The four issues the review should find. |

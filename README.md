# Lakehouse Review

A GitHub Action that reviews pull requests to **Databricks Asset Bundles** against the live workspace the code deploys to.

A general code reviewer reads the diff. It cannot know that `orders.cust_id` is a `BIGINT` in production, that two dashboards read the column you are renaming, or that the table you are reading was deprecated last quarter. Lakehouse Review works out what the pull request touches in Databricks, reads those facts with a read-only identity, and asks **Genie Code** (with **Genie One** for business definitions) to review the change against them. Every finding cites the Databricks fact it is based on.

> **New to the project?** Start with [the setup guide](docs/setup.md). It walks through creating the reviewer identity, granting access, and adding the workflow.

## What Lakehouse Review checks

It only reports problems that need a fact from the workspace, and stays silent on style and generic code issues.

| Area | What's included |
| --- | --- |
| **Data contracts** | Tables or columns that do not exist, type mismatches in joins, writes that change an existing table's schema. |
| **Lineage and relationships** | Removed or renamed columns that break downstream tables, views, jobs, or dashboards. Joins that contradict a documented identifier relationship. |
| **Business semantics** | Metrics that differ from their governed definition, reads from deprecated or non-authoritative datasets, status codes used for the wrong purpose. |
| **Data correctness** | Measures summed at the wrong grain, for example an order total counted once per line item. |
| **Governance** | Classified columns (for example PII) copied into a table without the same classification. |
| **Deployment** | Unsupported Databricks Runtime versions, and deploys that would overwrite changes made outside the bundle. |

**Lakehouse Review does not replace your code reviewer.** It is a data-specific layer that runs alongside GitHub Copilot or any other reviewer. See [a sample review](docs/sample-review.md).

## How it works

On every push to a pull request, the action:

1. **Finds the impact.** It resolves the bundle on the base branch and the pull request branch, follows imports, and parses SQL and PySpark to find the tables and columns each task reads and writes.
2. **Collects evidence.** It reads only what it needs from the workspace: table metadata, tags, metric views, lineage, deployed job settings, and runtimes.
3. **Runs exact checks**, then asks **Genie Code** to review the change against the evidence in a read-only sandbox. With `analysis-depth: detailed`, it also asks **Genie One** what your Pages and metric views say about the joins, grains, and codes the change uses.
4. **Posts one comment** on the pull request, updated on every push, with the issues that need attention first. The full result is also written to `review.json`.

If the reviewer cannot read something, the comment still posts what it could check and lists the exact grant to add. See [the architecture](docs/architecture.md) for the details.

## Getting started

The full walkthrough is in [docs/setup.md](docs/setup.md). Here's the setup process at a glance:

| Step | What to do | Usually handled by |
| --- | --- | --- |
| 1 | Create a **separate, read-only** service principal for the reviewer. Do not reuse your deploy identity. | Databricks workspace admin |
| 2 | Run `lakehouse-review setup --principal <application-id>` from your bundle repository to see the grants it needs, then apply them. | Catalog owner or workspace admin |
| 3 | Allow the reviewer to use Genie Code through Unity Gateway and, for detailed analysis, Genie One. Share the Pages that define your business terms with it. | Databricks workspace admin |
| 4 | Run `lakehouse-review doctor` with the reviewer's profile to confirm everything works. | Platform team |
| 5 | Add the workspace URL, client ID, and client secret to the repository's variables and secrets. | GitHub admin |
| 6 | Add the workflow below and open a pull request. | Repository maintainer |

The setup command **shows what it would do by default**. Add `--run` when you're ready to apply the grants.

```yaml
# .github/workflows/lakehouse-review.yml
name: Lakehouse Review

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: guanjieshen/genie_code_code_review_gh_action@v0
        with:
          databricks-host: ${{ vars.DATABRICKS_HOST }}
          client-id: ${{ vars.LAKEHOUSE_REVIEW_CLIENT_ID }}
          client-secret: ${{ secrets.LAKEHOUSE_REVIEW_CLIENT_SECRET }}
          bundle-target: staging
```

The review is **advisory by default**. Set `fail-on: blocker` once you trust the results. Every other input is optional and listed in [docs/inputs.md](docs/inputs.md).

## Making changes after setup

| You want to… | Where to make the change |
| --- | --- |
| Fail the check on blocking issues | `fail-on` input, or `fail_on` in `.lakehouse-review/config.yml` |
| Check joins, grain, and codes against your business definitions | `analysis-depth: detailed`, and write Pages for your entities ([docs/ontology.md](docs/ontology.md)) |
| Give the reviewer team context, such as which catalog is production | `.lakehouse-review/instructions.md` |
| Turn a check off or change its severity | `checks` in `.lakehouse-review/config.yml` |
| Review a bundle in a subfolder of a monorepo | `bundle-dir` input, one job per bundle |
| Use a workspace that does not serve the Genie Code model | `genie-host` input |
| Run behind a proxy or package mirror | `HTTPS_PROXY`, `ca-bundle`, and `python-index-url` ([docs/setup.md](docs/setup.md#locked-down-networks)) |

The config file and instructions are **always read from the base branch**, so a pull request cannot change how it is reviewed. Changes to them take effect after they merge.

## What it supports

| Supported | Not yet supported |
| --- | --- |
| Databricks Asset Bundles with Python, SQL, and notebook (`.py`, `.sql`) tasks | Repositories without a bundle |
| Lakeflow pipelines with file or glob libraries | `.ipynb` notebooks, wheel and JAR tasks, dbt tasks |
| Monorepos, one bundle per job, using `bundle-dir` | Scala and R code |
| Unity Catalog workspaces on AWS. Azure is being validated ([notes](docs/setup.md#cloud-notes)) | Google Cloud (untested), Hive metastore tables |
| GitHub-hosted and self-hosted Linux runners | Windows runners, GitHub Enterprise Server |

Table names built at runtime (for example from a config file) are reported as unresolved in the **Analysis coverage** section.

> **Preview.** Lakehouse Review depends on the Genie Code CLI, which is in Beta, and is not covered by Databricks Support. Report problems in [GitHub issues](../../issues).

## Repository structure

| Path | Purpose |
| --- | --- |
| `action.yml` | The composite action: input validation, tool setup, the review step, and failure reporting. |
| `src/genie_review/` | The review engine. `pipeline.py` runs the stages; `checks.py` holds the check catalog. |
| `src/genie_review/resources/` | The reviewer charter and the JSON schema Genie Code must answer in. |
| `examples/` | A workflow and config file to copy, and two demos with planted issues: `demo/` (orders) and `tpch-ontology/`. |
| `tests/` | Unit tests, including a 12-finding regression fixture for the report layout. |
| `scripts/` | The Genie Code CLI installer and the live integration test (`.github/workflows/integration.yml`). |
| `docs/` | Setup, inputs, configuration, architecture, security, and operations guides. |

## Documentation

- **[Setup](docs/setup.md)** — Reviewer identity, grants, networking, and the first pull request.
- **[Inputs and outputs](docs/inputs.md)** — Every action input and output.
- **[Configuration](docs/configuration.md)** — The config file, team instructions, and merge policy.
- **[Ontology validation](docs/ontology.md)** — How detailed analysis uses Genie One, and how to write Pages it can use.
- **[Troubleshooting](docs/troubleshooting.md)** — Common setup problems and how to fix them.
- **[Data and privacy](docs/data-and-privacy.md)** — What is sent where.
- **[Costs](docs/costs.md)** — What a review uses, and how to control it.
- **[Security model](docs/security.md)** — How the action handles untrusted pull request content.
- **[Architecture](docs/architecture.md)** — The pipeline stages and the evidence model.
- **[Changelog](CHANGELOG.md)** — Changes between releases.
- **[License](LICENSE)** — Databricks License. Third-party notices are in [NOTICE](NOTICE).

## Running tests

From the repository root:

```bash
uv sync
uv run pytest
```

The tests run locally without Databricks workspace access. To review a local bundle repository without posting to GitHub:

```bash
uv run lakehouse-review run --repo ../my-bundle-repo --base-ref main --profile my-profile --no-publish
```

`--no-publish` prints the review and writes the impact graph, evidence, and prompt to `.lakehouse-review-out/`.

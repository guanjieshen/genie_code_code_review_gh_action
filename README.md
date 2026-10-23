# Lakehouse Review

![Status](https://img.shields.io/badge/status-preview-orange)
![Genie Code CLI](https://img.shields.io/badge/Genie%20Code%20CLI-v0.2.0-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)

## Overview

**Lakehouse Review** brings Databricks-aware code reviews with Genie-powered semantic analysis to GitHub pull requests. It reviews changes to Databricks code **against the live workspace the code deploys to**. It is a data-specific review layer that runs alongside your existing code reviewer (for example GitHub Copilot), not a replacement for it.

A general code reviewer reads the diff. It cannot know that `orders.cust_id` is a `BIGINT` in production, that a view and two dashboards read the column you are renaming, or that the table you are reading was deprecated last quarter. Lakehouse Review works out what the pull request touches in Databricks, reads those facts with a read-only identity, and asks **Genie Code** (with **Genie One** for business definitions) to review the change against them. Every finding cites the Databricks fact it is based on.

```
Pull request ──► Copilot (or any reviewer)  → code quality, logic, style
             └─► Lakehouse Review           → schemas, lineage, deployed state, governance, metric definitions
```

## What it checks

The action only reports problems that need a fact from the workspace. It stays silent on style and generic code issues. Every finding belongs to one review area:

| Review area | Example findings | Checks |
|---|---|---|
| Data contracts & compatibility | Missing table or column, type mismatch in a join, write changes an existing table's schema | `contract.unknown-object`, `contract.column-mismatch`, `contract.type-mismatch`, `contract.write-schema` |
| Lineage, relationships & ontology | A removed column breaks downstream tables, views, jobs, or dashboards; a join violates a documented identifier relationship | `lineage.downstream-break`, `ontology.identifier-relationship` (Genie One + Genie Code) |
| Business semantics & metrics | A metric differs from its governed definition; code reads a deprecated dataset; a status code is used for the wrong purpose | `semantic.metric-definition-drift`, `semantic.non-authoritative-source`, `semantic.filter-meaning` (Genie Code, Genie One) |
| Security & governance | Classified (for example PII) columns copied into an unclassified table | `governance.tagged-data-flow` (Genie Code) |
| Deployment & operations | Unsupported Databricks Runtime; deploy overwrites changes made outside the bundle | `deploy.runtime-support`, `deploy.drift` |
| Data correctness & quality | A measure summed at the wrong grain (for example an order total counted once per line item) | `correctness.aggregation-grain` (Genie One + Genie Code) |

Each finding has three separate ratings:

| Rating | Values | How it is set |
|---|---|---|
| **Severity** | critical, high, medium, low, info | How bad the issue would be |
| **Confidence** | confirmed, high, medium, low | From the evidence, not from the model: verified metadata or lineage is *confirmed*; a semantic finding anchored to a metric view or a certified or deprecated dataset is *high*; Genie interpretation alone, or a classification gap without permission evidence, is *medium* |
| **Merge impact** | blocker, action required, review recommended, informational | A configurable policy. By default only *critical*, *confirmed* contract, lineage, and deployment issues are blockers |

Related checks of the same root cause (for example a schema change and the downstream break it causes) are shown as one issue; the JSON output keeps every finding. See [a sample review](docs/sample-review.md).

## How it works

1. **Impact analysis.** The action resolves your Databricks Asset Bundle on the base branch and the pull request branch, follows Python imports, and parses SQL and PySpark to find the tables and columns each task reads and writes. It compares the two, so it knows a column was removed, not just that a line changed.
2. **Evidence.** It reads only what is needed from the workspace: table metadata, governance tags, metric views, downstream lineage, deployed job settings, and offered runtimes.
3. **Exact checks.** Checks that need no model run first.
4. **Genie review.** Genie Code reviews the pull request in a read-only sandbox with the plan and evidence. For semantic checks it asks Genie One. Findings that do not cite real evidence are dropped.
5. **Process and publish.** Findings get a review area, severity, confidence, and merge impact; related findings are grouped and ranked; the top issues are shown first. One comment on the pull request is updated on every push, and `review.json` holds the full structured result.

See [docs/architecture.md](docs/architecture.md) for details.

## Getting started

### 1. Create a reviewer service principal

Create a **separate, read-only** service principal for the reviewer. Use the same authentication method as your deploy workflow, but not the same identity: the reviewer passes pull request content to an AI agent, so it should only be able to read metadata.

Grant it the access in [docs/setup.md](docs/setup.md). The minimum is `USE CATALOG`, `USE SCHEMA`, and `BROWSE` on the catalogs your code uses, `CAN_VIEW` on the jobs your bundle deploys, and `CAN_USE` on one SQL warehouse.

### 2. Add repository variables and secrets

In your repository settings, add:

| Name | Type | Value |
|---|---|---|
| `DATABRICKS_HOST` | Variable | Workspace URL |
| `GENIE_REVIEWER_CLIENT_ID` | Variable | Service principal application ID |
| `GENIE_REVIEWER_CLIENT_SECRET` | Secret | Service principal OAuth secret |

### 3. Add the workflow

Create `.github/workflows/genie-data-review.yml`:

```yaml
name: Lakehouse Review

on:
  pull_request:
    paths: ["databricks.yml", "resources/**", "src/**", "**/*.sql", "**/*.py", "**/*.ipynb"]

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
          client-id: ${{ vars.GENIE_REVIEWER_CLIENT_ID }}
          client-secret: ${{ secrets.GENIE_REVIEWER_CLIENT_SECRET }}
          bundle-target: staging
```

> 💡 **Tip**
> If your organization requires actions pinned to a commit SHA, use `guanjieshen/genie_code_code_review_gh_action@<commit-sha> # v0.3.1`. All actions used inside this action are pinned by SHA.

### 4. Open a pull request

The review appears as a comment on the pull request. Start in advisory mode (the default), then set `fail-on` once you trust the results.

> 💡 **Tip**
> Starting a new project? The [Databricks GitHub Actions template](https://github.com/guanjieshen/databricks-github-actions-template) has this workflow and a bundle deploy workflow ready to go. To see the review on a pull request with planted issues, follow the [demo](examples/demo/README.md).

## Inputs

| Input | Default | Description |
|---|---|---|
| `databricks-host` | required | Workspace the code deploys to |
| `client-id` | required | Reviewer service principal application ID |
| `client-secret` | empty | OAuth secret. Leave empty to use GitHub OIDC federation (add `id-token: write` permission) |
| `bundle-target` | bundle default | Bundle target to review against |
| `bundle-dir` | `.` | Folder containing `databricks.yml`, for monorepos. Config paths are relative to it |
| `warehouse-id` | auto | SQL warehouse for metadata queries |
| `analysis-depth` | `rapid` | `rapid`: exact checks plus one compact Genie Code review. `detailed`: full evidence plus ontology questions to Genie One (slower, more tokens) |
| `semantic` | `genie-one` | `genie-one` or `off` (turns off semantic checks and ontology questions) |
| `fail-on` | `none` | Fail the check on any `blocker` (merge-impact policy), or at a severity: `critical`, `high`, `medium` |
| `fail-on-error` | `false` | Fail the check when the review cannot run (sign-in or bundle errors). By default it explains the problem and passes |
| `genie-model` | Genie default | Override the Genie Code model |
| `genie-host` | empty | Separate workspace that serves the Genie Code model (see below) |
| `genie-client-id` / `genie-client-secret` | `client-id` / `client-secret` | Identity for `genie-host` |
| `on-model-unavailable` | `deterministic` | `deterministic`, `skip`, or `fail` |
| `config-file` | `<bundle-dir>/.genie-review/config.yml` | Repo config file, read from the base branch |
| `instructions` | empty | Extra team context for the reviewer |
| `python-index-url` | pypi.org | Package index for runners that cannot reach pypi.org (for example an Artifactory or Nexus mirror). Dependencies are hash-pinned |
| `genie-version` | `v0.2.0` | Genie Code CLI release |
| `upload-artifacts` | `false` | Upload impact graph, evidence, and prompt for debugging |

## Configuration

Teams tune the review with an optional config file. It is always read from the **base branch**, so a pull request cannot change how it is reviewed.

```yaml
# .genie-review/config.yml
target: staging
fail_on: critical
semantic: genie-one
max_findings: 15
checks:
  disable: [deploy.runtime-support]
  severity:
    lineage.downstream-break: critical
instructions_file: .genie-review/instructions.md
```

`instructions.md` is plain text the reviewer reads as team context, for example "the `main` catalog is production" or "finance owns all `gold_*` tables". It adds context; it cannot change the review rules or output format. See [docs/configuration.md](docs/configuration.md).

## Ontology validation

Some business meaning lives outside table metadata: what a metric means, which identifier is canonical for an entity, and how two identifier systems relate. Lakehouse Review decides which of those questions a pull request raises, from what it touches:

| The PR... | Question | Check |
|---|---|---|
| Newly joins two tables, or joins them on different keys | What is the documented relationship, and which keys must be used (including composite keys)? | `ontology.identifier-relationship` |
| Joins two identifiers of the same entity (for example `customer_id` and `cust_id`) | What is the canonical identifier, and how do these relate? | `ontology.identifier-relationship` |
| Sums a measure after joining a finer-grained table on that table's key | At what grain is the measure defined; does this double count? | `correctness.aggregation-grain` |
| Filters on a status, flag, or type code to define an output | What does the code mean; is it the right column? | `semantic.filter-meaning` |
| Writes a metric-like column (revenue, LTV, margin) | What is the governed definition, and which asset is authoritative? | `semantic.metric-definition-drift` |
| Reads a metric-like table with no certified or deprecated signal | Is this the authoritative source? | `semantic.non-authoritative-source` |

Questions that Unity Catalog already answers (a metric view that defines the measure, a certified or deprecated tag) are not sent anywhere. In `detailed` mode, the rest go to **Genie One**, up to a budget (default 6), riskiest first. Answers become evidence with their citations. An answer counts as authoritative only when it cites a governed source (a Unity Catalog Page, a metric view, or a certified dataset); an uncited answer is a hypothesis and cannot create a high-confidence finding on its own. Genie Code then gives a **verdict** for every answered question (`conflict`, `no_conflict`, or `unclear`). A missing verdict is reported as unresolved, and a conflict always becomes a finding, so a definition the PR violates cannot be silently dropped. The **Analysis coverage** section shows how many questions were planned, answered by Unity Catalog, asked, answered authoritatively, and how the verdicts came out.

> 💡 **Tip**
> Ontology checks catch errors that pass every type check, such as joining customers to orders on the order key (both `BIGINT`). See the [TPC-H ontology demo results](docs/ontology-evaluation.md).

## Model availability

Genie Code's default model is not offered in every workspace. The action checks before each review and picks a route:

1. Genie Code's default model (or `genie-model`) in the target workspace.
2. If you set `genie-host`, Genie Code in that workspace. Data, evidence, and Genie One stay on the target workspace; only the reviewer model runs there.
3. If no model is reachable, the action posts the exact checks and impact summary, and notes that the AI review was skipped (`on-model-unavailable: deterministic`).

The review comment always states which route was used. All model calls go through Genie Code and Genie One.

## When access is missing

The review always returns something useful. If the reviewer identity cannot read something, the comment still posts every finding it could make, and adds an **Access the reviewer is missing** table that names what it could not read, which checks were limited, and the exact grant to add for that service principal. If the review cannot run at all (for example sign-in fails or the bundle does not resolve), the comment explains why and how to fix it. Neither case blocks the pull request unless you set `fail-on-error: true`.

## Security

- **Read-only identity.** Read-only is enforced by the service principal's grants, not by trusting the agent.
- **Base-branch trust.** Config, team instructions, and agent configuration files (`AGENTS.md`, `.agents/`, `.genie/`, `*.rules`) are taken from the base branch before Genie runs.
- **Sandboxed agent.** Genie Code runs with a read-only sandbox (no file writes, no network from shell commands). Only the Genie One tools needed for questions are allowed.
- **Untrusted input.** Pull request content and workspace metadata are passed as data, not instructions. Model output is schema-validated, must cite evidence, and is stripped of links, images, HTML, and mentions before posting.
- **Token separation.** Genie never receives the GitHub token.
- **Forks.** Pull requests from forks are skipped, because they do not receive secrets.

> ⚠️ **Warning**
> Review comments include table and column names from your workspace. In a public repository, those comments are public.

See [docs/security.md](docs/security.md).

## Limitations

- The Genie Code CLI is in Beta. This action pins a tested release.
- Runners need to reach github.com (Genie Code CLI download), your Python package index, and the Databricks workspace. If setup fails, the action posts a comment that lists the likely cause.
- One bundle per workflow run. In a monorepo, add one job (or workflow) per bundle with `bundle-dir` and a `paths` filter.
- v0.1 supports Databricks Asset Bundles with Python, SQL, and notebook tasks, and Lakeflow pipelines with file or glob libraries. Repos without a bundle are not supported yet.
- Table names built dynamically at runtime (for example from a config file) are reported as unresolved in the coverage section.
- Lineage only shows objects the reviewer identity can see.
- `deploy.drift` needs a target with a shared `workspace.root_path` (typical for staging and production). Targets in `mode: development` deploy to a per-user path the reviewer cannot resolve.

## Development

```bash
uv sync
uv run pytest
uv run genie-review run --repo ../my-bundle-repo --base-ref main --profile my-profile --no-publish
```

`--no-publish` prints the review and writes the impact graph, evidence, and prompt to `.genie-review-out/`.

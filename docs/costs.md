# Costs

A review uses three billable services in **your** Databricks workspace. Lakehouse Review itself has no charge.

| Service | `rapid` (default) | `detailed` |
| --- | --- | --- |
| **Genie Code** through Unity Gateway | One short availability check, and one review with compact evidence and up to 15,000 characters of the diff | One short availability check, and one review with full evidence and up to 40,000 characters of the diff |
| **Genie One** | Not used | Up to 6 questions per pull request (`ontology.max_questions`), asked only when Unity Catalog cannot answer them |
| **SQL warehouse** | A few small metadata queries | The same, plus any queries Genie One runs to answer questions |

Reviews only call Genie Code when a check needs it. A pull request that touches no Databricks resources makes no model calls.

## How long a review takes

On the demo pull requests, a `rapid` review took about 15 to 40 seconds and a `detailed` review about 100 to 120 seconds. Genie One questions take about 35 to 60 seconds each and are asked in parallel. Runner time for installing the tools comes on top.

Genie One does not report token usage, so the cost per review has not been measured. Check your Unity Gateway and Genie One usage in the workspace after the first reviews.

## Controlling cost

| You want to… | How |
| --- | --- |
| Cap model spend for the reviewer | Set a Unity Gateway budget for the reviewer service principal |
| Ask Genie One fewer questions | Lower `ontology.max_questions` in the config file |
| Skip draft pull requests | Add `if: ${{ !github.event.pull_request.draft }}` to the job |
| Review only Databricks changes | Add a `paths` filter, for example `["databricks.yml", "resources/**", "src/**"]` |
| Stop superseded runs | Add a `concurrency` group with `cancel-in-progress: true` |
| Run `detailed` only when asked | Use a label, as below |

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review, labeled]

jobs:
  review:
    if: ${{ !github.event.pull_request.draft }}
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
          analysis-depth: ${{ contains(github.event.pull_request.labels.*.name, 'detailed-review') && 'detailed' || 'rapid' }}
```

## When to use each depth

**Use `rapid` for every push.** It runs every exact check (contracts, lineage, deployment) and one Genie Code review.

**Use `detailed` for changes to joins, aggregations, filters, or metrics**, or before merging to production. It is the only depth that checks the change against your business definitions, which catches errors that pass every type check. See the [ontology evaluation](ontology-evaluation.md).

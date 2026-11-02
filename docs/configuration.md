# Configuration

Most teams need no config file. When you want one, settings are resolved in this order: **action inputs**, then the **config file**, then defaults.

The config file and the instructions file are always read from the pull request's **base branch**, so a pull request cannot change how it is reviewed. Changes to them take effect after they merge.

## Config file

Create `.lakehouse-review/config.yml` in the bundle folder. Every setting is optional:

```yaml
target: staging                    # bundle target to review against
fail_on: none                      # none | blocker | critical | high | medium
analysis_depth: rapid              # rapid | detailed
semantic: genie-one                # genie-one | off
max_findings: 15                   # issues shown in the comment

checks:
  disable: [deploy.runtime-support]
  severity:
    lineage.downstream-break: critical

ontology:
  max_questions: 6                 # Genie One questions per pull request (detailed mode)

genie:
  model: gpt-5.6-sol               # a model your workspace serves

instructions_file: .lakehouse-review/instructions.md
```

| Setting | Default | Description |
| --- | --- | --- |
| `target` | the bundle's default target | Same as the `bundle-target` input. |
| `fail_on` | `none` | `blocker` fails on any blocking issue. A severity fails at that severity or above. |
| `analysis_depth` | `rapid` | `detailed` also asks Genie One about joins, grain, and codes. See [costs](costs.md). |
| `semantic` | `genie-one` | `off` turns off Genie One and the checks that need it. |
| `max_findings` | `15` | Maximum issues in the comment. `review.json` keeps every finding. |
| `checks.disable` | `[]` | Check ids to turn off. |
| `checks.severity` | `{}` | Severity overrides by check id. |
| `ontology.max_questions` | `6` | Genie One budget per pull request. |
| `genie.model` | `gpt-5.6-sol` | Genie Code model. |
| `instructions_file` | none | Path to team instructions, relative to the bundle folder. |

The file is checked when the review starts. Unknown settings, out-of-range numbers, and invalid YAML fail the check with a list of every problem.

## What the comment shows and what blocks a merge

These rules are fixed, so every team reads reviews the same way:

- **Shown:** every issue with medium confidence or better. Low-confidence observations and informational notes are in `review.json` only.
- **Grouped:** related checks with one root cause, such as a schema change and the downstream break it causes, are shown as one issue.
- **Needs attention:** up to 3 blocking or action-required issues with high confidence or better.
- **Blocker:** a **confirmed critical** issue about a data contract, lineage, or deployment. Blockers fail the check only when `fail_on: blocker`.

## Team instructions

`instructions.md` gives the reviewer context it cannot get from the workspace. Keep it short and factual:

```markdown
- The `main` catalog is production. `dev_*` catalogs are scratch.
- Finance owns all tables in `main.finance`. Changes to them need a migration note.
- `customer_id` is the standard key name. `cust_id` is legacy.
```

Instructions add context only. The review rules, the evidence requirement, and the output format cannot be changed by instructions.

## Check ids

| Area | Check ids |
| --- | --- |
| Data contracts | `contract.unknown-object`, `contract.column-mismatch`, `contract.type-mismatch`, `contract.write-schema` |
| Lineage and relationships | `lineage.downstream-break`, `ontology.identifier-relationship` |
| Business semantics | `semantic.metric-definition-drift`, `semantic.non-authoritative-source`, `semantic.filter-meaning` |
| Data correctness | `correctness.aggregation-grain` |
| Governance | `governance.tagged-data-flow` |
| Deployment | `deploy.runtime-support`, `deploy.drift` |

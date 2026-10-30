# Configuration

Settings are resolved in this order: **action inputs**, then the **config file**, then defaults. The config file and instructions file are always read from the pull request's **base branch**. Changes to them in a pull request take effect after it merges.

### Config file

Create `.lakehouse-review/config.yml` in the bundle folder. The file is checked when the review starts: unknown values, out-of-range numbers, and invalid YAML fail the check with a list of every problem. Unknown top-level keys are ignored with a warning in the log.

> **Note:** `.genie-review/config.yml` from earlier versions is still read when `.lakehouse-review/config.yml` does not exist. Rename the folder; the old name stops working in v1.0.

```yaml
# Bundle target to review against (same as the bundle-target input)
target: staging

# Fail the check: none | blocker (merge-impact policy) | critical | high | medium
fail_on: none

# Semantic checks backend: genie-one | off
semantic: genie-one

max_findings: 15

checks:
  # Turn individual checks off
  disable: [deploy.runtime-support]
  # Override severities
  severity:
    lineage.downstream-break: critical

report:
  categories: [contracts, correctness, semantics, lineage, governance, deployment]
  min_severity: low                # hide findings below this severity
  prominent_min_confidence: high   # "Needs attention" only shows findings at or above this confidence
  show_low_confidence: false       # low-confidence observations stay in review.json only
  include_info: false
  group_related: true              # show related checks of one root cause as one issue
  max_priority: 3                  # findings expanded under "Needs attention"

merge_policy:
  block_severities: [critical]
  block_min_confidence: confirmed
  block_categories: [contracts, lineage, deployment]

analysis_depth: rapid              # rapid | detailed

ontology:
  enabled: true                    # plan ontology questions (Genie One is asked only in detailed mode)
  max_questions: 6                 # Genie One budget per PR

genie:
  model: ""                        # empty = gpt-5.6-sol; set a model your workspace serves
  on_model_unavailable: deterministic

instructions_file: .lakehouse-review/instructions.md
```

| Key | Default | Description |
|---|---|---|
| `target` | bundle default | Bundle target |
| `fail_on` | `none` | `blocker` fails on any blocker; a severity fails at that severity or above |
| `semantic` | `genie-one` | `genie-one` or `off` |
| `max_findings` | `15` | Maximum issues in the comment |
| `analysis_depth` | `rapid` | `rapid` or `detailed` |
| `ontology.enabled` / `ontology.max_questions` | `true` / `6` | Ontology question planning and Genie One budget |
| `report.*` | see above | What the comment shows. `review.json` always keeps every finding |
| `merge_policy.*` | see above | Which findings are blockers. Blockers only fail the check when `fail_on: blocker` |
| `checks.disable` | `[]` | Check ids to turn off |
| `checks.severity` | `{}` | Severity overrides by check id |
| `genie.model` | `gpt-5.6-sol` | Genie Code model |
| `genie.on_model_unavailable` | `deterministic` | `deterministic`, `skip`, or `fail` |
| `instructions_file` | none | Path to team instructions |

### Team instructions

`instructions.md` gives the reviewer context it cannot get from the workspace. Keep it short and factual:

```markdown
- The `main` catalog is production. `dev_*` catalogs are scratch.
- Finance owns all tables in `main.finance`. Changes to them need a migration note.
- `customer_id` is the standard key name. `cust_id` is legacy.
```

Instructions add context only. The review rules, the evidence requirement, and the output format cannot be changed by instructions.

### Check ids

| Area | Check ids |
| --- | --- |
| Data contracts | `contract.unknown-object`, `contract.column-mismatch`, `contract.type-mismatch`, `contract.write-schema` |
| Lineage and relationships | `lineage.downstream-break`, `ontology.identifier-relationship` |
| Business semantics | `semantic.metric-definition-drift`, `semantic.non-authoritative-source`, `semantic.filter-meaning` |
| Data correctness | `correctness.aggregation-grain` |
| Governance | `governance.tagged-data-flow` |
| Deployment | `deploy.runtime-support`, `deploy.drift` |

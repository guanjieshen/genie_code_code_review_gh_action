# Configuration

Settings are resolved in this order: **action inputs**, then the **config file**, then defaults. The config file and instructions file are always read from the pull request's **base branch**. Changes to them in a pull request take effect after it merges.

### Config file

Create `.genie-review/config.yml`:

```yaml
# Bundle target to review against (same as the bundle-target input)
target: staging

# Fail the check at this severity or above: none | critical | high | medium
fail_on: none

# Semantic checks backend: genie-one | off
semantic: genie-one

max_findings: 15
min_confidence: 0.6

checks:
  # Turn individual checks off
  disable: [deploy.runtime-support]
  # Override severities
  severity:
    lineage.downstream-break: critical

genie:
  model: ""                        # leave empty for Genie Code's default
  on_model_unavailable: deterministic

instructions_file: .genie-review/instructions.md
```

| Key | Default | Description |
|---|---|---|
| `target` | bundle default | Bundle target |
| `fail_on` | `none` | Severity that fails the check |
| `semantic` | `genie-one` | `genie-one` or `off` |
| `max_findings` | `15` | Maximum findings in the comment |
| `min_confidence` | `0.6` | Minimum confidence for Genie findings (exact checks always count) |
| `checks.disable` | `[]` | Check ids to turn off |
| `checks.severity` | `{}` | Severity overrides by check id |
| `genie.model` | Genie default | Genie Code model override |
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

See the [README](../README.md#what-it-checks) for the full list.

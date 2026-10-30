# Inputs and outputs

Every input of the action. Only `databricks-host` and `client-id` are required. Most teams also set `client-secret` and `bundle-target`, and leave the rest at their defaults.

Inputs are checked before anything is installed. If one is not valid, the action posts a comment that lists every problem and fails the check.

> **Tip:** Settings that can also go in `.lakehouse-review/config.yml` are marked **config**. An input always wins over the config file. See [configuration](configuration.md).

## Workspace and identity

| Input | Default | Description |
| --- | --- | --- |
| `databricks-host` | required | Workspace URL the code deploys to, for example `https://adb-123.11.azuredatabricks.net`. Must be `https://`. |
| `client-id` | required | Application ID of the read-only reviewer service principal. |
| `client-secret` | empty | OAuth secret of the reviewer. Leave empty to use GitHub OIDC federation (experimental, see [setup](setup.md#github-oidc-federation)). |
| `warehouse-id` | first usable serverless warehouse | SQL warehouse for metadata queries (tags and metric views). |

## What to review

| Input | Default | Description |
| --- | --- | --- |
| `bundle-target` | the bundle's default target | Bundle target to review against. **config:** `target`. |
| `bundle-dir` | `.` | Folder that contains `databricks.yml`, relative to the repository root. Use it in monorepos. Must stay inside the repository. |
| `config-file` | `<bundle-dir>/.lakehouse-review/config.yml` | Path to the config file, read from the base branch. If set, the file must exist. `.genie-review/config.yml` is still read if the new path is absent. |
| `instructions` | empty | Extra team context for the reviewer, added to the base-branch instructions file. |

## Depth and gating

| Input | Default | Description |
| --- | --- | --- |
| `analysis-depth` | `rapid` | `rapid`: exact checks plus one compact Genie Code review. `detailed`: full evidence plus ontology questions to Genie One. See [costs](costs.md). **config:** `analysis_depth`. |
| `semantic` | `genie-one` | `genie-one` or `off`. `off` turns off Genie One and the checks that need it. **config:** `semantic`. |
| `fail-on` | `none` | `none` only comments. `blocker` fails on any issue the merge policy rates as blocking. `critical`, `high`, or `medium` fail at that severity or above. **config:** `fail_on`. |
| `fail-on-error` | `false` | `true` fails the check when the review cannot run, for example when sign-in fails. By default the comment explains the problem and the check passes. Invalid configuration always fails. |

## Genie Code model

| Input | Default | Description |
| --- | --- | --- |
| `genie-model` | `gpt-5.6-sol` | Genie Code model. If your workspace does not serve it, set one it does serve. `lakehouse-review doctor` shows which route works. **config:** `genie.model`. |
| `genie-host` | empty | A separate workspace that serves the Genie Code model, when the target workspace does not. Only the model runs there; evidence, Genie One, and data stay on `databricks-host`. |
| `genie-client-id` | `client-id` | Service principal for `genie-host`. |
| `genie-client-secret` | `client-secret` | OAuth secret for `genie-host`. |
| `on-model-unavailable` | `deterministic` | What to do when no Genie Code model is reachable: `deterministic` posts the exact checks only, `skip` posts nothing, `fail` fails the check. **config:** `genie.on_model_unavailable`. |
| `genie-version` | `v0.2.0` | Genie Code CLI release to install. Only tested releases are supported. |

## Runner and network

| Input | Default | Description |
| --- | --- | --- |
| `python-index-url` | pypi.org | Package index for runners that cannot reach pypi.org, for example an Artifactory or Nexus mirror. Packages are hash-pinned, so a mirror cannot substitute them. |
| `ca-bundle` | empty | Path to a PEM file with extra trusted certificates, for networks that inspect TLS. Relative paths start at the repository root. Applied to every tool the action runs. |
| `upload-artifacts` | `false` | Upload `review.json`, the impact graph, evidence, and prompt as a workflow artifact. They contain table and column metadata, so keep this off in public repositories. |
| `github-token` | `${{ github.token }}` | Token used to post the review comment. Needs `pull-requests: write`. |

For an HTTP proxy, set `HTTPS_PROXY` (and `NO_PROXY` if needed) as job environment variables. Every tool the action runs reads them.

## Outputs

| Output | Description |
| --- | --- |
| `status` | `blocker`, `action_required`, `review_recommended`, `informational`, `no_issues`, `no_issues_partial` (some checks could not run), or `not_run` (the review could not run). Empty if setup failed before the review step. |
| `issues` | Number of issues in the review. Related findings of one root cause count as one issue. |
| `blockers` | Number of issues the merge policy rates as blocking. |
| `review-json` | Path to `review.json` on the runner, with every finding, including those hidden from the comment. |
| `findings` | Number of individual findings. |
| `critical` | Number of critical findings. |
| `comment-url` | URL of the review comment. |

For example, to request a human data review when the action finds a blocker:

```yaml
      - id: review
        uses: guanjieshen/genie_code_code_review_gh_action@v0
        with:
          databricks-host: ${{ vars.DATABRICKS_HOST }}
          client-id: ${{ vars.LAKEHOUSE_REVIEW_CLIENT_ID }}
          client-secret: ${{ secrets.LAKEHOUSE_REVIEW_CLIENT_SECRET }}
      - if: fromJSON(steps.review.outputs.blockers || '0') > 0
        run: gh pr edit "${{ github.event.pull_request.number }}" --add-label needs-data-review
        env:
          GH_TOKEN: ${{ github.token }}
```

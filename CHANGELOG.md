# Changelog

Lakehouse Review follows [semantic versioning](https://semver.org). Until v1.0, minor versions can include breaking changes; they are listed under **Breaking** with upgrade steps.

The `v0` tag always points to the latest v0 release. To pin a release, use its tag or, if your organization requires it, its commit SHA with the version as a comment:

```yaml
- uses: guanjieshen/genie_code_code_review_gh_action@<commit-sha> # v0.4.0
```

## v0.5.1

- The orders demo no longer ships example Pages. Lakehouse Review uses the Pages and metric views your workspace already has.

## v0.5.0

Fewer options and simpler code. The review itself is unchanged.

**Breaking**

| Removed | What to do instead |
| --- | --- |
| `on-model-unavailable` input and `genie.on_model_unavailable` | Nothing. When no Genie Code model is reachable, the exact checks always post. Use `fail-on-error` to fail the check instead. |
| `genie-client-id` and `genie-client-secret` inputs | Add the reviewer service principal to the `genie-host` workspace; it signs in with `client-id` and `client-secret`. |
| `instructions` input | Put team context in the base-branch file named by `instructions_file`. |
| `report.*` and `merge_policy.*` settings | Nothing. The rules are now fixed (see [configuration](docs/configuration.md#what-the-comment-shows-and-what-blocks-a-merge)) and match the old defaults. |
| `ontology.enabled`, `limits.*`, `preset` | Use `semantic: off` or `analysis-depth: rapid`; move `limits.max_findings` and `limits.fail_on` to the top level. |
| `.genie-review/` config folder and `genie-review` command | Rename the folder to `.lakehouse-review/`; use `lakehouse-review`. |

Unknown settings in the config file now fail the check with a clear error, instead of being ignored.

**Changed**

- Genie Code gets no tools. Genie One is asked only by the action's ontology planner, and Genie Code reads the answers as evidence.
- The pipeline, evidence collection, ontology planner, and config loader are split into small functions. Lint runs in CI.

## v0.4.1

- Ontology checks use the definitions the workspace already has (Pages, metric views, certified datasets) through Genie One. The project no longer ships example ontology content: the TPC-H demo and its Pages are removed.
- The integration test uses the orders demo.

## v0.4.0

Makes the action ready for teams outside Databricks to set up and run on their own.

**Breaking**

- The config folder is now `.lakehouse-review/`. `.genie-review/` is still read when the new folder is absent, with a warning; it stops working in v1.0. To upgrade, rename the folder.
- The CLI is now `lakehouse-review`. `genie-review` still works and will be removed in v1.0.
- The debug artifact is now named `lakehouse-review`.
- Invalid inputs or configuration now fail the check with a list of every problem, instead of failing with a stack trace or being ignored.

**Added**

- `lakehouse-review setup` prints the least-privilege grants for the reviewer (`USE CATALOG` and `BROWSE` on each catalog the bundle uses; validated without `SELECT` or `USE SCHEMA`), and applies them with `--run`.
- `lakehouse-review doctor` checks configuration, sign-in, the bundle, deployment state, the warehouse, Unity Catalog access, the Genie Code model, and Genie One, with a fix for each problem.
- Outputs `status`, `issues`, `blockers`, and `review-json`, so workflows can branch on the result.
- `ca-bundle` input for networks that inspect TLS. Proxies work through `HTTPS_PROXY`.
- Inputs are validated before anything is installed.
- Docs: inputs, troubleshooting, data and privacy, costs, and ontology validation.

**Fixed**

- Reports longer than GitHub's 65,536-character comment limit are shortened to fit; the job summary keeps the full report.
- A target that was never deployed is no longer reported as missing access.
- Page citations no longer use a bolded sentence as the Page title.
- Access fixes recommended `READ METADATA`, which is not a Unity Catalog privilege. They now recommend `BROWSE`.

## v0.3.1 (2026-10-23)

- Ontology checks for identifier relationships, aggregation grain, and filter meaning.
- Genie Code gives a verdict for every answered ontology question; a conflict always becomes a finding.

## v0.3.0 (2026-10-22)

- `analysis-depth: rapid | detailed`. Detailed mode asks Genie One ontology questions planned from the change.
- Report consistency pass.

## v0.2.0 (2026-10-22)

- Renamed to Lakehouse Review. New PR comment with review areas, grouped issues, and a "Needs attention" section.
- Confidence is derived from evidence, not from the model. **Breaking:** in `findings.json`, `confidence` is now a label (`confirmed`, `high`, `medium`, `low`); the model's score moved to `model_confidence`.
- Configurable merge-impact policy and `fail-on: blocker`.

## v0.1.0 to v0.1.6 (2026-10-22)

- First release, then: public package index guard, notebook widget parameters, monorepo support with `bundle-dir`, missing-access explanations, configurable package index, and service principal sign-in.

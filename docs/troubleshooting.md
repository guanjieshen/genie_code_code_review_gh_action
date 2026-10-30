# Troubleshooting

Start with `lakehouse-review doctor` (see [setup](setup.md#4-check-the-setup)). It checks sign-in, the bundle, grants, the Genie Code model, and Genie One, and prints a fix for each problem.

When the review cannot run, the pull request comment says **Review could not run** with the cause and the fix. The check passes unless you set `fail-on-error: true`. Invalid configuration always fails the check.

## The review did not start

| Symptom | Cause | Fix |
| --- | --- | --- |
| "Some action inputs are not valid" | An input has a value the action does not accept | Fix the listed inputs in the workflow file. Every problem is listed at once. |
| "No databricks.yml in `.`" | The repository was not checked out, or the bundle is in a subfolder | Add `actions/checkout` with `fetch-depth: 0` before the action, and set `bundle-dir`. |
| "The review could not start on this runner" | A setup step failed before the review | Open the job log and find the failing **Initialize** step, then see the rows below. |
| **Initialize: install dependencies** fails, for example a `hatchling` or connection error | The runner cannot reach pypi.org | Set `python-index-url` to your package mirror. |
| **Initialize: install Genie Code CLI** fails | The runner cannot reach github.com, or the release does not exist | Allow the download, check `genie-version`, or use a self-hosted runner with access. |
| TLS or certificate errors in any step | A proxy inspects TLS | Set `ca-bundle` to your company's certificate file. |
| Nothing happens on pull requests from forks | Forks do not receive secrets, so the action skips them | Expected. Run the review on a branch in the same repository. |
| Nothing happens on `push` or `pull_request_target` | The action only runs on `pull_request` | Use `on: pull_request`. Do not use `pull_request_target`. |

## The review could not run

| Symptom | Cause | Fix |
| --- | --- | --- |
| "Could not sign in" | Wrong host, client ID, or secret, or the service principal is not in the workspace | Check the three values, and that the principal was added to the workspace. With OIDC, check the federation policy and `id-token: write`. |
| "The bundle does not resolve" | `databricks bundle validate` fails for the reviewer | Run it locally with the reviewer profile. Common causes: a required variable without a default, a wrong `bundle-target`, or a lookup the reviewer cannot read. |
| "The configuration is not valid" | A value in `.lakehouse-review/config.yml` is wrong | The config file is read from the base branch, so **merge the fix first**, or point `config-file` at another file. |

## The review ran, but something is missing

| Symptom | Cause | Fix |
| --- | --- | --- |
| **Partial review** with an "Access the reviewer is missing" table | The reviewer cannot read some objects | Apply the grants in the table, or run `lakehouse-review setup`. |
| "AI review engine: unavailable" | No Genie Code model is reachable for the reviewer | Allow Genie Code through Unity Gateway, set `genie-model` to a model the workspace serves, or set `genie-host`. The exact checks still ran. |
| "Business relationships and definitions were not validated" | `analysis-depth` is `rapid`, `semantic` is `off`, or Genie One is unavailable | Use `analysis-depth: detailed` and check that service principals may use Genie One. |
| An ontology finding is **low confidence** although a Page defines the rule | The reviewer cannot open the Page, or the Page title does not name the entity or schema | Share the Page with the reviewer, and follow [writing Pages](ontology.md#writing-pages-the-reviewer-can-use). |
| No lineage findings for a table you know has consumers | Lineage is recorded when jobs and queries run, and only shows objects the reviewer can see | Run the downstream jobs at least once, and grant `BROWSE` on the downstream catalogs. |
| "target not deployed yet" in the coverage section | The reviewed target was never deployed | Expected for new targets. The drift check runs after the first deploy. |
| "Bundle deployment state" in the missing access table | The reviewer cannot read the target's `root_path` | Grant `CAN_READ` on it. Targets in `mode: development` use a per-user folder; review a shared target instead. |
| Some tables are listed as **unresolved** | The code builds table names at runtime | Use bundle variables or job parameters for table names, or accept the gap. |

## The comment

| Symptom | Cause | Fix |
| --- | --- | --- |
| "Details were shortened to fit GitHub's comment size limit" | The full report is longer than 65,536 characters | The job summary and `review.json` have the full report. Consider raising `report.min_severity`. |
| A change to the config file had no effect | The config file is read from the base branch | Expected. It takes effect after the pull request merges. |
| Several comments from Lakehouse Review | Two workflows, or two jobs, review the same pull request | Each run updates one comment. Use one job per bundle and a `concurrency` group. |

## Getting help

Run with `upload-artifacts: true` to keep `review.json`, the impact graph, the evidence, and the prompt for the failing run. **They contain table and column metadata**, so do not share them publicly. Report problems in GitHub issues, without workspace URLs or credentials.

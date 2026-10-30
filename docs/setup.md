# Setup

This guide creates the read-only identity the action uses, grants it the access each check needs, and adds the workflow to your repository.

> **Before you start:** you need a Databricks workspace with Unity Catalog, a repository with a Databricks Asset Bundle (`databricks.yml`), the [Databricks CLI](https://docs.databricks.com/dev-tools/cli/install.html), and [uv](https://docs.astral.sh/uv/) to run the setup commands locally.

## Steps at a glance

| Step | What to do | Usually handled by |
| --- | --- | --- |
| 1 | [Create the reviewer service principal](#1-create-the-reviewer-service-principal) and an OAuth secret for it. | Databricks workspace admin |
| 2 | [Grant Unity Catalog and workspace access](#2-grant-access) with `lakehouse-review setup`. | Catalog owner or workspace admin |
| 3 | [Allow Genie Code, Genie One, and Pages](#3-allow-genie-code-genie-one-and-pages). | Databricks workspace admin |
| 4 | [Check the setup](#4-check-the-setup) with `lakehouse-review doctor`. | Platform team |
| 5 | [Add variables and secrets to GitHub](#5-add-variables-and-secrets-to-github). | GitHub admin |
| 6 | [Add the workflow](#6-add-the-workflow) and open a pull request. | Repository maintainer |

To run the setup commands, clone this repository next to your bundle repository:

```bash
git clone https://github.com/guanjieshen/genie_code_code_review_gh_action lakehouse-review
cd lakehouse-review && uv sync
```

## 1. Create the reviewer service principal

Create a **dedicated** service principal for the reviewer, for example `lakehouse-reviewer`, in **Settings > Identity and access > Service principals**, and generate an OAuth secret for it. Note the **application ID**.

```bash
databricks service-principals create --display-name lakehouse-reviewer
```

**Do not reuse your deploy identity.** The reviewer passes pull request content to an AI agent, so it should only be able to read metadata. Read-only is enforced by its grants, not by trusting the agent.

## 2. Grant access

From your bundle repository, run `setup` with the profile of someone who can grant access (not the reviewer):

```bash
uv run --project ../lakehouse-review lakehouse-review setup \
  --profile <admin-profile> --principal <application-id> --target staging
```

It reads the bundle, finds every catalog the code reads or writes, and prints the grants and permissions to apply. **Nothing is changed by default.** Add `--run` to apply them with your identity, or hand the output to the right team.

| Access | Grant | Used for |
| --- | --- | --- |
| Each catalog the code uses | `USE CATALOG`, `BROWSE` | Names, columns, comments, tags, metric views, and lineage, without reading data |
| One SQL warehouse | `CAN_USE` | Tags and metric views |
| Jobs the bundle deployed | `CAN_VIEW` | Drift between the deployed job and the bundle |
| The target's `workspace.root_path` | `CAN_READ` | Mapping bundle resources to deployed jobs |

**The reviewer never needs `SELECT`, `USE SCHEMA`, `MODIFY`, or ownership.** This minimum was validated with a service principal on GitHub Actions. If a catalog is missing, pass `--schema catalog.schema` for a schema in it.

> **Note:** Targets in `mode: development` deploy to a per-user folder the reviewer cannot read. Review a target with a shared `root_path`, such as staging or production, to get the drift check.

## 3. Allow Genie Code, Genie One, and Pages

These steps use the workspace UI and cannot be scripted by `setup`:

- **Genie Code.** Allow the reviewer to use Genie Code coding agents through Unity Gateway in the workspace that serves the model, and set a budget ([costs](costs.md)). If the target workspace does not serve the model, see [cloud notes](#cloud-notes).
- **Genie One.** For `analysis-depth: detailed`, confirm that service principals may use Genie One in the workspace. If they cannot, set `semantic: off`; the exact checks and the Genie Code review still run.
- **Pages.** Share the Pages that define your entities, identifiers, and metrics with the reviewer. **A Page the reviewer cannot open is not used as evidence**, even if you can see it. See [ontology validation](ontology.md).

## 4. Check the setup

Create a CLI profile for the reviewer service principal, then run `doctor` from your bundle repository:

```bash
uv run --project ../lakehouse-review lakehouse-review doctor --profile <reviewer-profile> --target staging
```

```
OK    Configuration     loaded from .lakehouse-review/config.yml; analysis-depth rapid, fail-on none
OK    Sign-in           <application-id> on https://<workspace>
OK    Bundle            target `staging` resolves; code uses 3 schema(s)
OK    Deployment state  deployed resources found; drift check can run
OK    SQL warehouse     `<warehouse-id>`
OK    Unity Catalog     all 3 schema(s) readable
OK    Genie Code        `gpt-5.6-sol` via target workspace (https://<workspace>)
OK    Genie One         reachable
```

Each line that is not `OK` includes the fix. `doctor` exits with an error only when the review could not produce useful results, such as a sign-in failure.

## 5. Add variables and secrets to GitHub

In the repository's **Settings > Secrets and variables > Actions**, add:

| Name | Type | Value |
| --- | --- | --- |
| `DATABRICKS_HOST` | Variable | Workspace URL |
| `LAKEHOUSE_REVIEW_CLIENT_ID` | Variable | Reviewer application ID |
| `LAKEHOUSE_REVIEW_CLIENT_SECRET` | Secret | Reviewer OAuth secret |

## 6. Add the workflow

Copy [`examples/lakehouse-review.yml`](../examples/lakehouse-review.yml) to `.github/workflows/` and open a pull request. The review appears as a comment within a few minutes.

**Start in advisory mode** (the default). Once you trust the results, set `fail-on: blocker` and make the check required in your branch protection rules.

If your organization requires actions pinned to a commit SHA, pin it with a version comment:

```yaml
      - uses: guanjieshen/genie_code_code_review_gh_action@<commit-sha> # v0.4.0
```

Every action used inside Lakehouse Review is pinned by SHA.

## GitHub OIDC federation

> **Experimental.** This path is implemented but has not been validated end to end yet.

To avoid a long-lived secret, leave `client-secret` empty. The action then signs in with GitHub's OIDC token. You need:

1. A service principal federation policy for the reviewer (see "Enable workload identity federation for GitHub Actions" in the Databricks documentation), with issuer `https://token.actions.githubusercontent.com` and a subject that matches your pull request workflow, for example `repo:<org>/<repo>:pull_request`. Creating it needs an account admin.
2. `id-token: write` in the workflow's `permissions`.

## Cloud notes

| Cloud | Status |
| --- | --- |
| **AWS** | Validated end to end, as a user and as a service principal on GitHub Actions. |
| **Azure** | Being validated. Some workspaces do not serve the default Genie Code model; use `genie-model` for a model the workspace serves, or `genie-host` for a workspace that serves it. |
| **Google Cloud** | Not tested. |

When you set `genie-host`, only the Genie Code model runs in that workspace. Evidence, Genie One, and data access stay on `databricks-host`. The reviewer needs a profile on both, which `genie-client-id` and `genie-client-secret` provide if it is a different principal.

## Locked-down networks

The runner needs to reach:

| Destination | Why | If it is blocked |
| --- | --- | --- |
| `github.com` | Download the Genie Code CLI release | Use a self-hosted runner with access, or allow the release download |
| `pypi.org`, `files.pythonhosted.org` | Install the action's Python dependencies | Set `python-index-url` to your mirror |
| Your Databricks workspace | Everything else | Allow it, or set a proxy |

If you use a proxy, set `HTTPS_PROXY` and `NO_PROXY` as job environment variables. If the proxy inspects TLS, set `ca-bundle` to your company's certificate file.

```yaml
jobs:
  review:
    runs-on: [self-hosted, linux]
    env:
      HTTPS_PROXY: http://proxy.example.com:8080
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: guanjieshen/genie_code_code_review_gh_action@v0
        with:
          databricks-host: ${{ vars.DATABRICKS_HOST }}
          client-id: ${{ vars.LAKEHOUSE_REVIEW_CLIENT_ID }}
          client-secret: ${{ secrets.LAKEHOUSE_REVIEW_CLIENT_SECRET }}
          python-index-url: https://artifactory.example.com/api/pypi/pypi/simple
          ca-bundle: .github/certs/company-ca.pem
```

Self-hosted runners need Linux or macOS with `bash`, `git`, `curl`, `tar`, and `jq`. The action installs `uv`, Python, and the Databricks CLI itself.

## If you cannot use a public action

Some organizations only allow actions from their own GitHub organization. You can copy the action into your repository and use it as a local action:

```bash
git clone --depth 1 --branch v0.4.0 https://github.com/guanjieshen/genie_code_code_review_gh_action /tmp/lakehouse-review
mkdir -p .github/actions && cp -R /tmp/lakehouse-review .github/actions/lakehouse-review
rm -rf .github/actions/lakehouse-review/.git
```

```yaml
      - uses: ./.github/actions/lakehouse-review
```

You then own upgrades. Check the [changelog](../CHANGELOG.md) before copying a new release.

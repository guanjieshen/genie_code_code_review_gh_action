# Setup

This guide creates the read-only identity the action uses and grants it the access each check needs.

> ⚠️ **Warning**
> v0.1 was validated end to end with a user identity. The grants below are the intended minimum for a service principal and are being validated. If a check reports "not readable by the reviewer identity" in the coverage section, add the grant listed for that check.

### 1. Create the service principal

Create a service principal named `genie-pr-reviewer` in your workspace (Settings > Identity and access > Service principals), then generate an OAuth secret for it. Note the **application ID**.

Alternatively, with the Databricks CLI:

```bash
databricks service-principals create --display-name genie-pr-reviewer
```

### 2. Grant Unity Catalog access

Run the following for each catalog your bundle reads or writes. `BROWSE` lets the reviewer see names, comments, tags, and lineage without reading data.

```sql
GRANT USE CATALOG, BROWSE ON CATALOG main TO `<application-id>`;
GRANT USE SCHEMA ON SCHEMA main.sales TO `<application-id>`;
```

The reviewer never needs `SELECT` on your data, `MODIFY`, or ownership.

### 3. Grant workspace access

| Resource | Permission | Used by |
|---|---|---|
| Jobs deployed by the bundle | `CAN_VIEW` | `deploy.drift` |
| One SQL warehouse | `CAN_USE` | Tags and metric views (information schema queries) |
| Bundle deployment folder (`/Workspace/Users/.../.bundle/...` or your `root_path`) | `CAN_READ` | Mapping bundle resources to deployed jobs |

### 4. Allow Genie Code and Genie One

The reviewer identity must be able to use Genie Code through Unity Gateway in the workspace that serves the model, and to call the Genie One MCP server for semantic checks. Check your workspace's Unity Gateway permissions and budgets, and confirm that service principals are allowed to use Genie One. If they are not, set `semantic: off`; the exact checks and the Genie Code review still run.

### 5. Check the setup

From a machine with the Databricks CLI and the Genie Code CLI installed, create a profile for the service principal and run:

```bash
uv run lakehouse-review doctor --profile genie-pr-reviewer --repo . --base-ref main
```

It reports whether a Genie Code model is reachable and which route the action will use.

### 6. Add it to GitHub

Add `DATABRICKS_HOST` and `GENIE_REVIEWER_CLIENT_ID` as repository variables and `GENIE_REVIEWER_CLIENT_SECRET` as a secret, then add the workflow from the [README](../README.md#3-add-the-workflow).

> 💡 **Tip**
> To avoid a long-lived secret, configure GitHub OIDC federation for the service principal and leave `client-secret` empty. Add `id-token: write` to the workflow permissions.

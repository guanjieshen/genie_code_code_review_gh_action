# Demo: a pull request with planted data issues

This demo shows Lakehouse Review on a realistic pull request. It adds a small orders ETL bundle to a repository created from the [Databricks GitHub Actions template](https://github.com/guanjieshen/databricks-github-actions-template), creates sample tables (with comments, tags, a deprecated table, and a governed metric view), and opens a pull request with 11 issues that need workspace context to spot.

### 1. Create a repository from the template

Create a repository from [databricks-github-actions-template](https://github.com/guanjieshen/databricks-github-actions-template) (**Use this template**) and clone it. Follow its README to add the repository variables and secrets.

### 2. Add the demo project

From the root of your new repository, replace the example job with the demo project:

```bash
git clone --depth 1 https://github.com/guanjieshen/genie_code_code_review_gh_action /tmp/lakehouse-review
rm resources/job.yml src/trips_daily.py
cp -R /tmp/lakehouse-review/examples/demo/project/. .
```

### 3. Point it at your workspace

Edit the `catalog` and `warehouse_id` defaults in `databricks.yml`. The demo creates the `genie_review_demo` schema in that catalog.

Grant the reviewer service principal read access to the demo schema:

```sql
GRANT USE CATALOG, BROWSE ON CATALOG <catalog> TO `<reviewer-application-id>`;
GRANT USE SCHEMA ON SCHEMA <catalog>.genie_review_demo TO `<reviewer-application-id>`;
```

### 4. Commit and push to main

```bash
git add -A && git commit -m "Add Lakehouse Review demo" && git push
```

If the deploy workflow is configured, it deploys the `prod` target. Wait for it to finish before the next step.

### 5. Create the demo data

The setup script creates the tables, deploys and runs the `prod` job so lineage is recorded, and then edits the deployed job to simulate an on-call hotfix.

```bash
DATABRICKS_CONFIG_PROFILE=<your-profile> python3 demo/setup.py
```

### 6. Open the demo pull request

```bash
./demo/create_demo_pr.sh
```

The review posts as a comment within a few minutes. Compare it with [demo/EXPECTED.md](project/demo/EXPECTED.md).

> 💡 **Tip**
> Turn on GitHub Copilot code review on the same pull request to see the two layers side by side. Copilot covers the plain logic bug (revenue counted once per order item); Lakehouse Review covers the issues that need the workspace.

# Data and privacy

Lakehouse Review runs on your GitHub runner and talks only to **your Databricks workspace** and **GitHub**. It does not send data to any other service, and it adds no telemetry of its own.

## What is sent where

| Destination | What is sent | Why |
| --- | --- | --- |
| **Your Databricks workspace** (`databricks-host`) | Table, column, and job names the pull request uses; metadata, tag, lineage, and `information_schema` queries | To collect the evidence for the review |
| **Genie Code, through Unity Gateway** in your workspace (or `genie-host`) | The review prompt: the plan, the evidence, up to 15,000 characters of the diff (40,000 in `detailed`), and team instructions. Genie Code can also read files of the checked-out repository in a read-only sandbox | To review the change |
| **Genie One** in your workspace (only with `analysis-depth: detailed`) | Up to `ontology.max_questions` questions about the tables, columns, joins, and codes the change uses | To check business definitions |
| **GitHub** | The review comment, the job summary, and the action outputs. With `upload-artifacts: true`, also the evidence and the prompt as a workflow artifact | To report the review |

The action also downloads the Genie Code CLI from `github.com/databricks/genie-code-cli` and Python packages from your package index. For the Genie Code CLI's own behavior, see its documentation.

## What is never sent

- **Table data.** The reviewer needs no `SELECT`. Genie One may run queries in your workspace to answer a question, with the reviewer's permissions. Their results stay in the workspace, and only the answer text and its citations are used.
- **Secrets.** Genie Code never receives the GitHub token. The Databricks credentials are written to a temporary config file readable only by the runner user, and are not part of the prompt or the artifacts.
- **Other repositories or branches.** Only the pull request's diff and the checked-out files are used.

## Where results are visible

- **The comment** contains table names, column names, and short explanations. **In a public repository, it is public.**
- **The job summary** has the same content as the comment, visible to anyone who can see the workflow runs.
- **Artifacts** (`upload-artifacts: true`) contain the impact graph, all evidence, and the prompt. Leave this off in public repositories.

## Retention

Lakehouse Review stores nothing between runs. The comment, job summary, and artifacts follow your GitHub retention settings. Unity Gateway and Genie One usage is recorded in your workspace like any other use of those services.

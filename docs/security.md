# Security model

Lakehouse Review passes untrusted pull request content to an AI agent. The design assumes the agent can be misled, and limits what that could cause.

### Controls

| Layer | Control |
|---|---|
| Identity | A dedicated read-only service principal. Read-only is enforced by its Unity Catalog and workspace grants. It needs no `SELECT` on data, no `MODIFY`, and no manage permissions. |
| Trust boundary | The config file, instructions, and agent configuration (`AGENTS.md`, `AGENTS.override.md`, `.agents/`, `.genie/`, `.codex/`, `*.rules`) come from the base branch. A pull request cannot add a skill, rewrite instructions, or loosen rules for its own review. |
| Sandbox | Genie Code runs with `--sandbox read-only`: shell commands cannot write files or reach the network. Databricks facts are fetched by the action before Genie runs, or through the Genie One MCP server. |
| Tool allowlist | Only `genie_ask`, `genie_poll_response`, and `genie_get_query_result` are enabled on Genie One. |
| Prompt structure | Rules come first. Pull request content and workspace metadata are passed inside delimited data blocks with a random per-run id, and the agent is told to treat them as data and report injection attempts. |
| Output | Genie's output must match a JSON schema, only planned checks are accepted, every finding must cite evidence that exists, and text is stripped of links, images, HTML, and mentions before posting. |
| Token separation | Genie never receives the GitHub token. Only the publish step uses it. |
| Events | Runs on `pull_request` only. Fork pull requests are skipped. Do not use `pull_request_target`. |

### Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).

### Data exposure

See [data and privacy](data-and-privacy.md) for what is sent where. In short:

- Review comments contain table names, column names, and short explanations. In a public repository they are public.
- Debug artifacts (`upload-artifacts: true`) contain the impact graph, evidence, and prompt. Leave this off in public repositories.
- Workspace comments and tags are written by people with permissions on those objects. They are treated as untrusted data, like pull request content.

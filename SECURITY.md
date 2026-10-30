# Security policy

## Reporting a vulnerability

**Please do not report security problems in public issues.** Use GitHub's private vulnerability reporting instead: on this repository, open **Security > Advisories > Report a vulnerability**.

Include what you found, how to reproduce it, and which version you used. Do not include workspace URLs, credentials, or data from your workspace.

## Supported versions

Security fixes are made in the latest release. Pin a release tag or commit SHA, and upgrade when a fix is published in the [changelog](CHANGELOG.md).

## Scope

Lakehouse Review passes untrusted pull request content to an AI agent. Reports about the following are especially welcome:

- A pull request that changes how its own review is configured or run.
- Pull request or workspace content that makes the agent ignore its rules, leak data, or post links, images, or mentions.
- Secrets reaching the prompt, the comment, the logs, or the artifacts.

See the [security model](docs/security.md) for the controls that are meant to prevent these.

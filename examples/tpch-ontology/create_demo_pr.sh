#!/usr/bin/env bash
# Opens the demo pull request. See EXPECTED.md for what the review should find.
set -euo pipefail
cd "$(dirname "$0")"

branch="demo/tpch-ontology"
git checkout -b "$branch"
cp -R pr/. .
git add src resources
git commit -m "Add fulfilled order revenue and supplier margin by nation"
git push -u origin "$branch"
gh pr create --title "Add fulfilled order revenue and supplier margin" \
  --body "Demo pull request with four planted ontology errors. See EXPECTED.md for what Lakehouse Review should find."

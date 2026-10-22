#!/usr/bin/env bash
# Opens a pull request with planted data issues. See demo/EXPECTED.md for what the review should find.
set -euo pipefail
cd "$(dirname "$0")/.."

branch="demo/planted-issues"
git checkout -b "$branch"
cp -R demo/pr/. .
git add src resources
git commit -m "Add segment revenue v2 and exec KPIs; rename cust_id to customer_id"
git push -u origin "$branch"
gh pr create --title "Add segment revenue v2 and exec KPIs" \
  --body "Demo pull request with planted data issues. See demo/EXPECTED.md for what Genie Data Review should find."

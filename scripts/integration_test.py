"""Live integration test: review the TPC-H demo pull request against a real workspace.

Builds a throwaway git repository from examples/tpch-ontology (base commit, then the demo PR commit),
runs `lakehouse-review run --no-publish`, and checks review.json. Needs a Databricks CLI profile for the
reviewer, the Databricks CLI, and the Genie Code CLI.

    uv run python scripts/integration_test.py --profile <reviewer> --depth rapid
    uv run python scripts/integration_test.py --profile <reviewer> --depth detailed --min-issues 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples/tpch-ontology"
ONTOLOGY_CHECKS = {"ontology.identifier-relationship", "correctness.aggregation-grain", "semantic.filter-meaning"}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=it", "-c", "user.email=it@example.com", "-c", "core.hooksPath=/dev/null", *args],
                   cwd=repo, check=True, capture_output=True)


def build_repo(dest: Path, catalog: str, schema: str, warehouse_id: str) -> None:
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns("pr", "EXPECTED.md", "README.md", "create_demo_pr.sh", "pages"))
    yml = dest / "databricks.yml"
    text = yml.read_text()
    for var, value in (("catalog", catalog), ("schema", schema), ("warehouse_id", warehouse_id)):
        text = re.sub(rf"(\n  {var}:\n(?:    .*\n)*?    default: ).*", rf"\g<1>{json.dumps(value)}", text)
    yml.write_text(text)
    git(dest, "init", "-q", "-b", "main")
    git(dest, "add", "-A")
    git(dest, "commit", "-qm", "base")
    git(dest, "checkout", "-qb", "demo")
    shutil.copytree(DEMO / "pr", dest, dirs_exist_ok=True)
    git(dest, "add", "-A")
    git(dest, "commit", "-qm", "Add fulfilled order revenue and supplier margin")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--profile", required=True)
    ap.add_argument("--depth", choices=["rapid", "detailed"], default="rapid")
    ap.add_argument("--catalog", default="main")
    ap.add_argument("--schema", default="lakehouse_review_demo")
    ap.add_argument("--warehouse-id", default="")
    ap.add_argument("--genie-bin", default="genie")
    ap.add_argument("--min-issues", type=int, default=0, help="Fail if the review reports fewer issues")
    ap.add_argument("--keep", action="store_true", help="Keep the temporary repository and output")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="lakehouse-review-it-"))
    repo, out = tmp / "repo", tmp / "out"
    build_repo(repo, a.catalog, a.schema, a.warehouse_id or "<warehouse-id>")
    cmd = [sys.executable, "-m", "genie_review.cli", "run", "--repo", str(repo), "--base-ref", "main", "--profile", a.profile,
           "--depth", a.depth, "--genie-bin", a.genie_bin, "--out-dir", str(out), "--no-publish"]
    if a.warehouse_id:
        cmd += ["--warehouse-id", a.warehouse_id]
    r = subprocess.run(cmd, env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)
    sys.stderr.write(r.stderr[-4000:])

    problems: list[str] = []
    review_path = out / "review.json"
    if not review_path.exists():
        problems.append(f"no review.json (exit {r.returncode})")
        review = {}
    else:
        review = json.loads(review_path.read_text())
    checks = sorted({f["check"] for f in review.get("findings", []) if f.get("visible")})
    print(f"status={review.get('status_key')} issues={review.get('issues')} checks={checks}")
    print(f"route: {review.get('route', '')}")
    if r.returncode != 0:
        problems.append(f"CLI exited {r.returncode}")
    if review and review.get("status_key") == "not_run":
        problems.append("review did not run")
    if review and (review.get("issues") or 0) < a.min_issues:
        problems.append(f"expected at least {a.min_issues} issues, got {review.get('issues')}")
    if a.depth == "detailed" and review and not (ONTOLOGY_CHECKS & set(checks)):
        problems.append("detailed review produced no ontology findings")

    if a.keep or problems:
        print(f"kept: {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    for p in problems:
        print(f"FAIL: {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

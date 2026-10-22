"""Set up the demo: create tables, deploy and run the prod target, then simulate an on-call edit to the job.

Usage (from the repo root):
    DATABRICKS_CONFIG_PROFILE=<profile> python3 demo/setup.py

Requires the Databricks CLI. Uses only the Python standard library.
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def cli(*args: str) -> str:
    r = subprocess.run(["databricks", *args], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[-600:])
    return r.stdout


def run_sql(statement: str, warehouse: str) -> None:
    body = json.dumps({"statement": statement, "warehouse_id": warehouse, "wait_timeout": "50s", "on_wait_timeout": "CONTINUE"})
    r = json.loads(cli("api", "post", "/api/2.0/sql/statements", "--json", body))
    while r["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(3)
        r = json.loads(cli("api", "get", f"/api/2.0/sql/statements/{r['statement_id']}"))
    if r["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(r["status"].get("error", {}).get("message", r["status"]["state"]))


def first_ok(warehouse: str, *statements: str) -> bool:
    """Workspaces have different tag policies; try each variant until one is accepted."""
    for s in statements:
        try:
            run_sql(s, warehouse)
            return True
        except RuntimeError:
            continue
    return False


def main() -> None:
    variables = json.loads(cli("bundle", "validate", "-t", "prod", "-o", "json"))["variables"]
    catalog, schema, warehouse = (variables[k].get("value") for k in ("catalog", "schema", "warehouse_id"))
    if not warehouse:
        sys.exit("Set the catalog, schema, and warehouse_id defaults in databricks.yml")
    t = f"{catalog}.{schema}"
    print(f"Demo tables: {t}  (warehouse {warehouse})")

    print("1/4 Creating demo tables")
    sql = (ROOT / "demo/setup_data.sql").read_text().replace("{{catalog}}", catalog).replace("{{schema}}", schema)
    for stmt in [s.strip() for s in sql.split(";\n") if s.strip()]:
        run_sql(stmt.rstrip(";"), warehouse)

    print("2/4 Tagging governed data")
    if not first_ok(warehouse,
                    f"ALTER TABLE {t}.bronze_customers ALTER COLUMN email SET TAGS ('pii' = 'EMAIL')",
                    f"ALTER TABLE {t}.bronze_customers ALTER COLUMN email SET TAGS ('data_classification' = 'pii')",
                    f"ALTER TABLE {t}.bronze_customers ALTER COLUMN email SET TAGS ('pii' = 'email')"):
        print("  Could not tag the email column. Add a PII tag that your tag policies allow to demo the governance check.")
    first_ok(warehouse, f"ALTER TABLE {t}.gold_revenue_daily_v1 SET TAGS ('deprecated' = 'true')")
    first_ok(warehouse, f"ALTER TABLE {t}.gold_revenue_daily SET TAGS ('certified' = 'true')",
             f"ALTER TABLE {t}.gold_revenue_daily SET TAGS ('certified')")

    print("3/4 Deploying and running the prod target (records lineage)")
    subprocess.run(["databricks", "bundle", "deploy", "-t", "prod"], cwd=ROOT, check=True)
    subprocess.run(["databricks", "bundle", "run", "-t", "prod", "orders_etl"], cwd=ROOT, check=True)

    print("4/4 Simulating an on-call edit to the deployed job")
    job_id = json.loads(cli("bundle", "summary", "-t", "prod", "-o", "json"))["resources"]["jobs"]["orders_etl"]["id"]
    cli("jobs", "update", "--json", json.dumps({"job_id": int(job_id), "new_settings": {
        "timeout_seconds": 7200, "tags": {"hotfix": "edited-in-ui"}}}))
    print("Done. Next: ./demo/create_demo_pr.sh")


if __name__ == "__main__":
    main()

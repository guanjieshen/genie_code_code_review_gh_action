"""Read-only evidence collection from the Databricks workspace.

Every fact gets a stable evidence id (ev:...). Findings must cite these ids.
"""

from __future__ import annotations

import re
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, PermissionDenied

from .access import AccessReport, classify
from .impact import ImpactResult
from .util import is_empty, log

DRIFT_FIELDS = ("timeout_seconds", "max_concurrent_runs", "tags", "schedule", "trigger", "continuous",
                "email_notifications", "webhook_notifications", "notification_settings", "health", "parameters")
DEV_PRESET_TAGS = {"dev"}  # added by bundle development mode


class Evidence:
    def __init__(self, principal: str = "<reviewer-application-id>") -> None:
        self.facts: dict[str, Any] = {}
        self.access = AccessReport(principal)  # what we could not read, and how to fix it

    @property
    def gaps(self) -> list[str]:
        return [g.area for g in self.access.gaps]

    def put(self, ev_id: str, fact: Any) -> None:
        self.facts[ev_id] = fact


def pick_warehouse(w: WorkspaceClient, preferred: str | None) -> str | None:
    if preferred:
        return preferred
    whs = list(w.warehouses.list())
    ranked = sorted(whs, key=lambda x: (not x.enable_serverless_compute, str(x.state) != "State.RUNNING"))
    return ranked[0].id if ranked else None


def _sql(w: WorkspaceClient, warehouse: str, q: str) -> list[dict[str, Any]]:
    r = w.statement_execution.execute_statement(statement=q, warehouse_id=warehouse, wait_timeout="50s")
    if r.status and r.status.error:
        raise RuntimeError(r.status.error.message)
    cols = [c.name for c in r.manifest.schema.columns] if r.manifest and r.manifest.schema else []
    return [dict(zip(cols, row)) for row in (r.result.data_array or [])] if r.result else []


def _lit(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def collect(w: WorkspaceClient, impact: ImpactResult, warehouse: str | None, principal: str = "<reviewer-application-id>") -> Evidence:
    ev = Evidence(principal)
    g = impact.graph
    tables = sorted(set(g.tables()))

    # ---- table metadata
    existing: set[str] = set()
    for t in tables:
        try:
            info = w.tables.get(t)
            existing.add(t)
            ev.put(f"ev:uc:{t}:metadata", {
                "exists": True,
                "type": info.table_type.value if info.table_type else None,
                "comment": info.comment,
                "owner": info.owner,
                "columns": [{"name": c.name, "type": c.type_text, "comment": c.comment} for c in info.columns or []],
                "view_definition": (info.view_definition or "")[:1500] or None,
            })
        except NotFound:
            ev.put(f"ev:uc:{t}:metadata", {"exists": False})
        except PermissionDenied:
            ev.put(f"ev:uc:{t}:metadata", {"exists": None, "access": "denied"})
            ev.access.table(t)
        except Exception as e:  # noqa: BLE001
            ev.put(f"ev:uc:{t}:metadata", {"exists": None, "access": classify(e)})
            ev.access.table(t)

    # ---- tags and metric views (information_schema, needs a warehouse)
    by_catalog: dict[str, set[str]] = {}
    for t in tables:
        c, s, _ = t.split(".")
        by_catalog.setdefault(c, set()).add(s)
    if warehouse:
        for c, schemas in by_catalog.items():
            in_list = ", ".join(_lit(s) for s in sorted(schemas))
            try:
                for r in _sql(w, warehouse, f"SELECT schema_name, table_name, tag_name, tag_value FROM `{c}`.information_schema.table_tags WHERE schema_name IN ({in_list})"):
                    ev.facts.setdefault(f"ev:uc:{c}.{r['schema_name']}.{r['table_name']}:tags", {"table_tags": {}, "column_tags": {}})["table_tags"][r["tag_name"]] = r["tag_value"]
                for r in _sql(w, warehouse, f"SELECT schema_name, table_name, column_name, tag_name, tag_value FROM `{c}`.information_schema.column_tags WHERE schema_name IN ({in_list})"):
                    ev.facts.setdefault(f"ev:uc:{c}.{r['schema_name']}.{r['table_name']}:tags", {"table_tags": {}, "column_tags": {}})["column_tags"].setdefault(r["column_name"], {})[r["tag_name"]] = r["tag_value"]
                for r in _sql(w, warehouse, f"SELECT table_schema, table_name FROM `{c}`.information_schema.tables WHERE table_type = 'METRIC_VIEW' AND table_schema IN ({in_list})"):
                    fq = f"{c}.{r['table_schema']}.{r['table_name']}"
                    mv = w.tables.get(fq)
                    if any(t in (mv.view_definition or "") for t in tables):
                        ev.put(f"ev:uc:{fq}:metric_view", {"comment": mv.comment, "definition": mv.view_definition})
            except Exception as e:  # noqa: BLE001
                ev.access.warehouse(warehouse, f"tags in {c}: {classify(e)}")
    else:
        ev.access.warehouse(None, "no SQL warehouse the reviewer can use")

    # ---- downstream lineage for existing tables this PR writes
    for t in g.tables_with("writes"):
        if t not in existing:
            continue
        node = g.nodes[f"uc:{t}"]
        try:
            lin = w.api_client.do("GET", "/api/2.0/lineage-tracking/table-lineage",
                                  body={"table_name": t, "include_entity_lineage": True})
        except Exception:  # noqa: BLE001
            ev.access.lineage(t)
            continue
        downs: list[dict[str, Any]] = []
        for d in lin.get("downstreams", []):
            ti = d.get("tableInfo")
            if ti:
                downs.append({"kind": ti.get("table_type", "TABLE"),
                              "name": f"{ti.get('catalog_name')}.{ti.get('schema_name')}.{ti.get('name')}"})
            downs += [{"kind": "JOB", "job_id": j.get("job_id")} for j in d.get("jobInfos", [])]
            downs += [{"kind": "NOTEBOOK", "notebook_id": n.get("notebook_id")} for n in d.get("notebookInfos", [])]
            downs += [{"kind": "DASHBOARD", "dashboard_id": x.get("dashboard_id")} for x in d.get("dashboardV3Infos", [])]
        ev.put(f"ev:lineage:{t}:downstream", {"downstream": downs})
        for col in (node.get("columns") or {}).get("removed", []):
            try:
                cl = w.api_client.do("GET", "/api/2.0/lineage-tracking/column-lineage", body={"table_name": t, "column_name": col})
                ev.put(f"ev:lineage:{t}.{col}:downstream", {"downstream_columns": [
                    f"{d['catalog_name']}.{d['schema_name']}.{d['table_name']}.{d['name']}" for d in cl.get("downstream_cols", [])]})
            except Exception:  # noqa: BLE001
                ev.access.lineage(t)

    # ---- deployed job state vs bundle on the base branch (drift)
    base_jobs = (impact.base_bundle.get("resources") or {}).get("jobs") or {}
    for nid, n in g.nodes.items():
        if n["kind"] != "job" or not n.get("deployed_id"):
            continue
        jkey = nid.split(".", 1)[1]
        try:
            dep = w.jobs.get(int(n["deployed_id"])).settings.as_dict()
        except Exception:  # noqa: BLE001
            ev.access.job(n["deployed_id"])
            continue
        base_job = base_jobs.get(jkey, {})
        drift = {}
        for f in DRIFT_FIELDS:
            dv, bv = dep.get(f), base_job.get(f)
            if f == "tags":
                dv = {k: v for k, v in (dv or {}).items() if k not in DEV_PRESET_TAGS or k in (bv or {})}
                bv = {k: v for k, v in (bv or {}).items() if k not in DEV_PRESET_TAGS}
            if f == "max_concurrent_runs" and bv is None:
                bv = 1
            if is_empty(dv) and is_empty(bv):
                continue
            if dv != bv:
                drift[f] = {"deployed": dv, "bundle_on_base": bv}
        dep_tasks = {t["task_key"] for t in dep.get("tasks", [])}
        base_tasks = {t["task_key"] for t in base_job.get("tasks", [])}
        if dep_tasks != base_tasks:
            drift["tasks"] = {"only_in_deployed": sorted(dep_tasks - base_tasks), "only_in_bundle": sorted(base_tasks - dep_tasks)}
        ev.put(f"ev:job:{n['deployed_id']}:drift", {"job_key": jkey, "drift": drift})

    # ---- runtime versions offered by the workspace
    head_jobs = (impact.head_bundle.get("resources") or {}).get("jobs") or {}
    wanted: set[str] = set()
    for job in head_jobs.values():
        wanted |= {c["new_cluster"].get("spark_version") for c in job.get("job_clusters", []) if c.get("new_cluster")}
        wanted |= {t["new_cluster"].get("spark_version") for t in job.get("tasks", []) if t.get("new_cluster")}
    wanted.discard(None)
    if wanted:
        try:
            offered = {v.key for v in w.clusters.spark_versions().versions or []}
        except Exception as e:  # noqa: BLE001
            ev.access.add("Databricks Runtime versions", "`deploy.runtime-support`", f"Workspace API error: {classify(e)}")
            offered = None
    if wanted and offered is not None:
        plain = re.compile(r"^\d+\.(\d+|x)\.x-scala2\.1[23]$")
        lts = sorted((v for v in offered if plain.match(v)), key=lambda v: [int(x) if x.isdigit() else 99 for x in v.split("-")[0].split(".")])
        for sv in sorted(wanted):
            ev.put(f"ev:workspace:spark_version:{sv}", {"offered_by_workspace": sv in offered, "examples_offered": lts[-5:]})

    # A job that exists on the base branch should already be deployed; if we found no id, we cannot see its state
    for nid, n in g.nodes.items():
        if n["kind"] == "job" and not n.get("deployed_id") and nid[4:].split(".", 1)[1] in base_jobs:
            ev.access.deployed_state()
    log(f"{len(ev.facts)} evidence facts, {len(ev.access.gaps)} access gaps")
    return ev

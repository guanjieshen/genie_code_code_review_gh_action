"""`lakehouse-review setup`: the grants and permissions the reviewer service principal needs.

By default it only prints what it would do, so an admin can review or hand the steps to the right team.
With `--run` it applies the Unity Catalog grants and workspace permissions with the caller's identity.
Steps that need the UI or an account admin (Unity Gateway, Genie One, Pages) are always printed, never run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from databricks.sdk import WorkspaceClient

from . import bundle as bdl
from .code_refs import file_refs
from .evidence import pick_warehouse
from .util import log


BUILTIN_CATALOGS = {"samples"}   # readable by every user; GRANT on it fails


@dataclass
class Step:
    what: str                 # plain-language description
    command: str              # SQL statement, CLI command, or manual instruction
    kind: str = "sql"         # sql | permission | manual
    apply: Callable[[], None] | None = field(default=None, repr=False)


@dataclass
class Plan:
    principal: str
    steps: list[Step] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def schemas_in_bundle(repo: Path, bundle_dir: str, profile: str, target: str | None) -> tuple[set[str], dict[str, Any]]:
    """Every catalog.schema the bundle's code reads or writes, plus the resolved bundle."""
    root = repo / bundle_dir
    resolved = bdl.resolve(root, profile, target)
    schemas: set[str] = set()
    for e in bdl.entries(resolved, root):
        path = root / e.file
        if not path.exists():
            continue
        refs = file_refs(path, e.params)
        for fqn in (*refs.reads, *refs.writes):
            parts = fqn.split(".")
            if len(parts) == 3 and "{" not in fqn:
                schemas.add(".".join(parts[:2]))
    return schemas, resolved


def _perm(object_type: str, object_id: str, principal: str, level: str) -> str:
    acl = {"access_control_list": [{"service_principal_name": principal, "permission_level": level}]}
    return f"databricks permissions update {object_type} {object_id} --json '{json.dumps(acl)}'"


def build(w: WorkspaceClient | None, principal: str, schemas: set[str], warehouse_id: str | None,
          resolved: dict[str, Any] | None, deployed: dict[str, dict[str, str]]) -> Plan:
    plan = Plan(principal)
    q = f"`{principal}`"

    def sql(stmt: str) -> Callable[[], None]:
        def run() -> None:
            assert w is not None and warehouse_id
            r = w.statement_execution.execute_statement(statement=stmt, warehouse_id=warehouse_id, wait_timeout="50s")
            if r.status and r.status.error:
                raise RuntimeError(r.status.error.message)
        return run

    def perm(object_type: str, object_id: str, level: str) -> Callable[[], None]:
        def run() -> None:
            assert w is not None
            w.api_client.do("PATCH", f"/api/2.0/permissions/{object_type}/{object_id}",
                            body={"access_control_list": [{"service_principal_name": principal, "permission_level": level}]})
        return run

    # ---- Unity Catalog: metadata, tags, and lineage. Never SELECT, MODIFY, or ownership.
    builtin = sorted({s for s in schemas if s.split(".")[0] in BUILTIN_CATALOGS})
    if builtin:
        plan.notes.append(f"{', '.join(f'`{s}`' for s in builtin)} are in a built-in catalog that every user can read; no grant needed.")
    schemas = schemas - set(builtin)
    for cat in sorted({s.split(".")[0] for s in schemas}):
        stmt = f"GRANT USE CATALOG, BROWSE ON CATALOG `{cat}` TO {q}"
        plan.steps.append(Step(f"Read names, comments, tags, and lineage in catalog `{cat}`", stmt + ";", "sql", sql(stmt)))
    # Validated 2026-10-30: USE CATALOG + BROWSE is enough for metadata, tags, and metric views; no USE SCHEMA.
    if not schemas:
        plan.notes.append("No tables were found in the bundle code. Pass `--schema catalog.schema` for each schema it uses.")

    # ---- workspace permissions
    if warehouse_id:
        plan.steps.append(Step(f"Run metadata queries on SQL warehouse `{warehouse_id}`",
                               _perm("warehouses", warehouse_id, principal, "CAN_USE"), "permission",
                               perm("warehouses", warehouse_id, "CAN_USE")))
    else:
        plan.notes.append("No SQL warehouse found. Give the reviewer `CAN_USE` on one and set `warehouse-id` in the workflow.")
    for key, job_id in sorted((deployed.get("jobs") or {}).items()):
        plan.steps.append(Step(f"Compare deployed job `{key}` with the bundle (drift check)",
                               _perm("jobs", job_id, principal, "CAN_VIEW"), "permission", perm("jobs", job_id, "CAN_VIEW")))
    root = ((resolved or {}).get("workspace") or {}).get("root_path")
    if root:
        if "/Users/" in root:
            plan.notes.append(f"The target deploys to a personal folder (`{root}`). Use a shared `workspace.root_path` for the "
                              "reviewed target so the reviewer can read its deployment state.")
        else:
            oid = None
            if w is not None:
                try:
                    oid = str(w.workspace.get_status(root).object_id)
                except Exception:  # noqa: BLE001 - not deployed yet
                    plan.notes.append(f"`{root}` does not exist yet. Run setup again after the first deploy.")
            if oid:
                plan.steps.append(Step(f"Read the bundle deployment state in `{root}`",
                                       _perm("directories", oid, principal, "CAN_READ"), "permission",
                                       perm("directories", oid, "CAN_READ")))

    # ---- always manual
    plan.steps += [
        Step("Use Genie Code through Unity Gateway",
             "In the workspace that serves the model, allow the reviewer to use Unity Gateway coding agents and set a budget.",
             "manual"),
        Step("Ask Genie One for business definitions (detailed analysis)",
             "Confirm service principals may use Genie One in this workspace. If not, set `semantic: off`.", "manual"),
        Step("Read the Pages that define your entities and metrics",
             "Share the relevant Pages with the reviewer. A Page the reviewer cannot open is not used as evidence.", "manual"),
    ]
    return plan


def render(plan: Plan, applied: dict[int, str] | None = None) -> str:
    out = [f"Reviewer setup for `{plan.principal}`", ""]
    sql_steps = [s for s in plan.steps if s.kind == "sql"]
    if sql_steps:
        out += ["Unity Catalog grants (run as a catalog owner or metastore admin):", ""]
        out += [f"  {s.command}" for s in sql_steps] + [""]
    perm_steps = [s for s in plan.steps if s.kind == "permission"]
    if perm_steps:
        out += ["Workspace permissions (run as a workspace admin or the object owner):", ""]
        out += [f"  # {s.what}\n  {s.command}" for s in perm_steps] + [""]
    out += ["Manual steps:", ""]
    out += [f"  - {s.what}: {s.command}" for s in plan.steps if s.kind == "manual"] + [""]
    if plan.notes:
        out += ["Notes:", ""] + [f"  - {n}" for n in plan.notes] + [""]
    if applied is not None:
        out += ["Result:", ""]
        out += [f"  {'OK  ' if r == 'ok' else 'FAIL'} {plan.steps[i].what}{'' if r == 'ok' else ': ' + r}" for i, r in sorted(applied.items())]
        out.append("")
    else:
        out += ["Nothing was changed. Add --run to apply the grants and permissions above with your identity.", ""]
    return "\n".join(out)


def apply(plan: Plan) -> dict[int, str]:
    results: dict[int, str] = {}
    for i, s in enumerate(plan.steps):
        if s.apply is None:
            continue
        try:
            s.apply()
            results[i] = "ok"
        except Exception as e:  # noqa: BLE001
            results[i] = str(e).splitlines()[0][:200]
    return results


def main(a: Any) -> int:
    w = WorkspaceClient(profile=a.profile)
    repo = Path(a.repo).resolve()
    schemas = set(a.schema or [])
    resolved: dict[str, Any] | None = None
    deployed: dict[str, dict[str, str]] = {}
    if not a.schema:
        try:
            found, resolved = schemas_in_bundle(repo, a.bundle_dir, a.profile, a.target)
            schemas |= found
        except Exception as e:  # noqa: BLE001
            log(f"Could not read the bundle ({str(e)[:160]}). Pass --schema catalog.schema instead.")
    if resolved is not None:
        deployed, _, _ = bdl.deployed_ids(repo / a.bundle_dir, a.profile, a.target)
    try:
        warehouse = pick_warehouse(w, a.warehouse_id)
    except Exception:  # noqa: BLE001
        warehouse = a.warehouse_id
    plan = build(w, a.principal, schemas, warehouse, resolved, deployed)
    results = apply(plan) if a.run else None
    print(render(plan, results))
    return 1 if results and any(r != "ok" for r in results.values()) else 0

"""Databricks Asset Bundle resolution: which files run in which jobs and pipelines, and what changed."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .util import sh

COMPUTE_KEYS = ("job_cluster_key", "new_cluster", "existing_cluster_id", "environment_key", "compute_key")
NOTEBOOK_EXTS = (".py", ".sql", ".ipynb", ".scala", ".r")


@dataclass
class Entry:
    """One executable unit: a job task or a pipeline library file."""

    resource: str            # e.g. jobs.orders_etl.silver, pipelines.orders
    kind: str                # task | pipeline
    file: str                # repo-relative entry file
    params: dict[str, str] = field(default_factory=dict)


def resolve(tree: Path, profile: str, target: str | None) -> dict[str, Any]:
    cmd = ["databricks", "bundle", "validate", "-o", "json", "-p", profile]
    if target:
        cmd += ["-t", target]
    return json.loads(sh(cmd, cwd=tree))


def deployed_ids(tree: Path, profile: str, target: str | None) -> dict[str, dict[str, str]]:
    """Bundle resource key -> deployed id, per resource type. Empty if never deployed."""
    cmd = ["databricks", "bundle", "summary", "-o", "json", "-p", profile]
    if target:
        cmd += ["-t", target]
    try:
        data = json.loads(sh(cmd, cwd=tree))
    except Exception:  # noqa: BLE001 - not deployed yet, or no permission
        return {}
    out: dict[str, dict[str, str]] = {}
    for rtype, items in (data.get("resources") or {}).items():
        out[rtype] = {k: str(v["id"]) for k, v in items.items() if isinstance(v, dict) and v.get("id")}
    return out


def _rel(path: str, files_root: str, tree: Path) -> str:
    if path.startswith("/"):
        rel = os.path.relpath(path, files_root)
    else:
        rel = path
    if not Path(rel).suffix:
        for ext in NOTEBOOK_EXTS:
            if (tree / f"{rel}{ext}").exists():
                return f"{rel}{ext}"
    return rel


def _task_entry(task: dict, files_root: str, tree: Path) -> tuple[str | None, dict[str, str]]:
    if "spark_python_task" in task:
        t = task["spark_python_task"]
        a = t.get("parameters", [])
        params = {a[i].lstrip("-"): a[i + 1] for i in range(len(a) - 1) if str(a[i]).startswith("--")}
        return _rel(t["python_file"], files_root, tree), params
    if "sql_task" in task and "file" in task["sql_task"]:
        t = task["sql_task"]
        return _rel(t["file"]["path"], files_root, tree), {k: str(v) for k, v in t.get("parameters", {}).items()}
    if "notebook_task" in task:
        t = task["notebook_task"]
        return _rel(t["notebook_path"], files_root, tree), {k: str(v) for k, v in t.get("base_parameters", {}).items()}
    return None, {}


def entries(bundle: dict[str, Any], tree: Path) -> list[Entry]:
    files_root = bundle.get("workspace", {}).get("file_path", "")
    res = bundle.get("resources", {})
    out: list[Entry] = []
    for jkey, job in (res.get("jobs") or {}).items():
        for task in job.get("tasks", []):
            f, params = _task_entry(task, files_root, tree)
            if f:
                out.append(Entry(f"jobs.{jkey}.{task['task_key']}", "task", f, params))
    for pkey, pipe in (res.get("pipelines") or {}).items():
        params = {k: str(v) for k, v in (pipe.get("configuration") or {}).items()}
        if pipe.get("catalog"):
            params["__default_catalog"] = pipe["catalog"]
        if pipe.get("schema") or pipe.get("target"):
            params["__default_schema"] = pipe.get("schema") or pipe.get("target")
        for lib in pipe.get("libraries", []):
            for key in ("file", "notebook"):
                if key in lib:
                    p = _rel(lib[key]["path"], files_root, tree)
                    out.append(Entry(f"pipelines.{pkey}", "pipeline", p, params))
            if "glob" in lib:
                inc = _rel(lib["glob"]["include"], files_root, tree)
                for p in sorted(tree.glob(inc)):
                    if p.is_file():
                        out.append(Entry(f"pipelines.{pkey}", "pipeline", str(p.relative_to(tree)), params))
    return out


def diff_resources(base: dict[str, Any], head: dict[str, Any]) -> dict[str, Any]:
    """Resource-level diff for jobs and pipelines, with the kind of change per task."""
    out: dict[str, Any] = {}
    for rtype in ("jobs", "pipelines"):
        b_all = (base.get("resources") or {}).get(rtype) or {}
        h_all = (head.get("resources") or {}).get(rtype) or {}
        for key in sorted(set(b_all) | set(h_all)):
            b, h = b_all.get(key), h_all.get(key)
            rid = f"{rtype}.{key}"
            if b is None or h is None:
                out[rid] = {"type": rtype, "change": "added" if b is None else "removed"}
                continue
            if b == h:
                continue
            item: dict[str, Any] = {"type": rtype, "change": "modified",
                                    "fields_changed": sorted(k for k in set(b) | set(h) if k != "tasks" and b.get(k) != h.get(k))}
            if rtype == "jobs":
                bc = {c["job_cluster_key"]: c for c in b.get("job_clusters", [])}
                hc = {c["job_cluster_key"]: c for c in h.get("job_clusters", [])}
                changed_clusters = {k for k in set(bc) | set(hc) if bc.get(k) != hc.get(k)}
                bt = {t["task_key"]: t for t in b.get("tasks", [])}
                ht = {t["task_key"]: t for t in h.get("tasks", [])}
                tasks = {}
                for tk in sorted(set(bt) | set(ht)):
                    if tk not in bt:
                        tasks[tk] = {"change": "added"}
                    elif tk not in ht:
                        tasks[tk] = {"change": "removed"}
                    else:
                        compute = any(bt[tk].get(k) != ht[tk].get(k) for k in COMPUTE_KEYS) or ht[tk].get("job_cluster_key") in changed_clusters
                        if bt[tk] != ht[tk] or compute:
                            tasks[tk] = {"change": "modified", "kinds": ["compute"] if compute else ["config"]}
                item["tasks"] = tasks
            out[rid] = item
    return out

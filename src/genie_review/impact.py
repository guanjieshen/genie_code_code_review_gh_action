"""Impact analysis: what in Databricks does this PR touch?

Runs the same extraction on the base tree and the head tree, then compares the two models.
That is how removals, renames, and type changes are found, not just "line 42 changed".
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import bundle as bdl
from .code_refs import CodeRefs, executed_files, file_refs, select_output_columns
from .models import ImpactGraph
from .util import log, sh

STATUS = {"A": "added", "M": "modified", "D": "removed", "R": "renamed", "C": "copied"}


@dataclass
class TreeModel:
    """Per-tree model: each executable entry with the files it runs and the data it touches."""

    entries: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class ImpactResult:
    graph: ImpactGraph
    base: TreeModel
    head: TreeModel
    base_bundle: dict[str, Any]
    head_bundle: dict[str, Any]
    changed_files: dict[str, str]
    refs: dict[str, CodeRefs]  # head entry key -> refs (for deterministic checks)
    repo: Path
    base_refs: dict[str, CodeRefs] = field(default_factory=dict)  # base entry key -> refs (what the PR changed)


def _model(tree: Path, bundle: dict[str, Any], bundle_dir: str) -> tuple[TreeModel, dict[str, CodeRefs]]:
    """Paths in the model are repo-relative, even when the bundle lives in a subfolder."""
    model, all_refs = TreeModel(), {}
    broot = tree / bundle_dir
    for e in bdl.entries(bundle, broot):
        e.file = os.path.normpath(os.path.join(bundle_dir, e.file))
        executes = executed_files(tree, e.file, [broot, broot / "src"])
        refs = file_refs(tree / e.file, e.params)
        helper_outputs: list[str] = []
        # Output columns come from shared helpers, or from the entry file itself when it writes one table
        own = {e.file} if e.file.endswith(".py") and len(refs.writes) == 1 else set()
        for f in sorted((executes - {e.file}) | own):
            if f.endswith(".py"):
                helper_outputs += select_output_columns((tree / f).read_text(errors="replace"))
        key = f"{e.resource}::{e.file}" if e.kind == "pipeline" else e.resource
        model.entries[key] = {
            "resource": e.resource, "kind": e.kind, "entry": e.file, "params": e.params,
            "executes": sorted(executes), "reads": refs.reads, "writes": refs.writes,
            "outputs": refs.outputs, "helper_outputs": helper_outputs,
        }
        all_refs[key] = refs
    return model, all_refs


def changed_files(repo: Path, base_ref: str) -> dict[str, str]:
    out = {}
    for line in sh(["git", "diff", "--name-status", f"{base_ref}...HEAD"], cwd=repo).splitlines():
        parts = line.split("\t")
        out[parts[-1]] = STATUS.get(parts[0][0], parts[0])
    return out


def analyze(repo: Path, base_ref: str, profile: str, target: str | None, bundle_dir: str = ".") -> ImpactResult:
    files = changed_files(repo, base_ref)
    log(f"{len(files)} changed files")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        base_tree = Path(tmp) / "base"
        sh(["git", "worktree", "add", "--detach", str(base_tree), base_ref], cwd=repo)
        try:
            if (base_tree / bundle_dir / "databricks.yml").exists():
                base_bundle = bdl.resolve(base_tree / bundle_dir, profile, target)
                base_model, base_refs = _model(base_tree, base_bundle, bundle_dir)
            else:  # bundle is new in this PR
                base_bundle, base_model, base_refs = {}, TreeModel(), {}
        finally:
            sh(["git", "worktree", "remove", "--force", str(base_tree)], cwd=repo, check=False)
    head_bundle = bdl.resolve(repo / bundle_dir, profile, target)
    head_model, head_refs = _model(repo, head_bundle, bundle_dir)
    resource_diff = bdl.diff_resources(base_bundle, head_bundle)

    g = ImpactGraph(target=head_bundle.get("bundle", {}).get("target", target or ""), job_diff=resource_diff)
    for f, st in files.items():
        g.node(f"file:{f}", "file", change=st)

    for key, e in head_model.entries.items():
        rtype, rkey = e["resource"].split(".")[0], ".".join(e["resource"].split(".")[1:3])
        rdiff = resource_diff.get(f"{rtype}.{rkey.split('.')[0]}", {})
        tdiff = rdiff.get("tasks", {}).get(e["resource"].split(".")[-1]) if e["kind"] == "task" else None
        touched = sorted(set(e["executes"]) & set(files))
        if not touched and not tdiff and not (e["kind"] == "pipeline" and rdiff):
            continue
        rid = f"res:{e['resource']}"
        g.node(rid, e["kind"], change=(tdiff or {}).get("change", "code_changed"), change_kinds=(tdiff or {}).get("kinds"),
               entry=e["entry"])
        for f in touched:
            g.edge(f"file:{f}", rid, "executed_by", "bundle:entry_file" if f == e["entry"] else "import_graph")
        how = "sql:sqlglot" if e["entry"].endswith(".sql") else "python:ast"
        for t, line in e["reads"].items():
            g.node(f"uc:{t}", "table")
            g.edge(rid, f"uc:{t}", "reads", how)
        base_e = base_model.entries.get(key)
        for t, line in e["writes"].items():
            n = g.node(f"uc:{t}", "table")
            g.edge(rid, f"uc:{t}", "writes", how)
            if not base_e or t not in base_e["writes"]:
                n.setdefault("change", "new_write")
                continue
            new_cols = e["outputs"].get(t) or e["helper_outputs"]
            old_cols = base_e["outputs"].get(t) or base_e["helper_outputs"]
            if new_cols and old_cols and set(new_cols) != set(old_cols):
                n.update(change="schema_changed", columns={
                    "removed": sorted(set(old_cols) - set(new_cols)), "added": sorted(set(new_cols) - set(old_cols))})
        for u in head_refs[key].unresolved:
            g.unresolved.append({"resource": e["resource"], "file": e["entry"], **u})

    for rid, d in resource_diff.items():
        g.node(f"res:{rid}", d["type"][:-1], change=d["change"], fields_changed=d.get("fields_changed"))
        for tk in d.get("tasks", {}):
            g.edge(f"res:{rid}", f"res:{rid}.{tk}", "contains", "bundle")
    deployed, g.deploy_state, g.deploy_detail = bdl.deployed_ids(repo / bundle_dir, profile, target)
    for rtype, ids in deployed.items():
        for k, did in ids.items():
            if f"res:{rtype}.{k}" in g.nodes:
                g.nodes[f"res:{rtype}.{k}"]["deployed_id"] = did
    return ImpactResult(g, base_model, head_model, base_bundle, head_bundle, files, head_refs, repo, base_refs)

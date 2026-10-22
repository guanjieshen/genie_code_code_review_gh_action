"""Check catalog, planner, and deterministic checks.

Every check passes the admission test: it needs a fact that lives in Databricks, not in the repo.
Deterministic checks run without any model; Genie reviews the planned checks on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .evidence import Evidence
from .impact import ImpactResult
from . import wording
from .models import Finding, PlanItem


@dataclass(frozen=True)
class Check:
    id: str
    preset: str          # core | semantic
    title: str
    guidance: str        # what Genie should look for (used in the prompt)


CATALOG: dict[str, Check] = {c.id: c for c in [
    Check("contract.unknown-object", "core", "Referenced object does not exist",
          "A table, view, or function the code reads does not exist in the target workspace and is not created by this PR."),
    Check("contract.column-mismatch", "core", "Column does not exist on the real table",
          "The code references a column that the live table does not have (and this PR does not add)."),
    Check("contract.type-mismatch", "core", "Join or comparison across incompatible types",
          "A join or comparison uses columns whose live types differ (for example BIGINT vs STRING), causing implicit casts or silent mismatches."),
    Check("contract.write-schema", "core", "Write changes an existing table's schema",
          "The data written does not match the existing target table: columns removed, renamed, or retyped, or a write mode that will fail or silently drop data."),
    Check("lineage.downstream-break", "core", "Change breaks downstream consumers",
          "A column or table this PR removes, renames, or retypes is used by downstream tables, views, jobs, notebooks, or dashboards."),
    Check("governance.tagged-data-flow", "core", "Governed data flows to an ungoverned target",
          "Columns carrying governance tags (for example PII classifications) are copied into a target that lacks the same tags or masking."),
    Check("deploy.drift", "core", "Deploy will overwrite changes made outside the bundle",
          "The deployed job differs from the bundle on the base branch (someone edited it in the UI); deploying this PR will overwrite those changes."),
    Check("deploy.runtime-support", "core", "Runtime not offered by the workspace",
          "A cluster spec uses a Databricks Runtime version the target workspace does not offer."),
    Check("semantic.metric-definition-drift", "semantic", "Metric computed differently from its governed definition",
          "The code computes a governed metric (from a metric view, certified table, or glossary) with different logic or filters."),
    Check("semantic.non-authoritative-source", "semantic", "Reads a deprecated or non-authoritative source",
          "The code reads a table that is deprecated or not the authoritative source for its concept, when an authoritative one exists."),
]}

TYPE_FAMILY = {
    "string": "string", "varchar": "string", "char": "string",
    "bigint": "number", "int": "number", "integer": "number", "smallint": "number", "tinyint": "number",
    "long": "number", "short": "number", "byte": "number", "decimal": "number", "double": "number", "float": "number",
    "date": "datetime", "timestamp": "datetime", "timestamp_ntz": "datetime", "boolean": "boolean",
}


def enabled_checks(preset: str, semantic: str, enable: list[str], disable: list[str]) -> set[str]:
    ids = {c.id for c in CATALOG.values() if c.preset == "core"}
    if semantic != "off":
        ids |= {c.id for c in CATALOG.values() if c.preset == "semantic"}
    ids |= {c for c in enable if c in CATALOG}
    return ids - set(disable)


# ------------------------------------------------------------------ planner


def plan(impact: ImpactResult, ev: Evidence, enabled: set[str]) -> list[PlanItem]:
    g, items = impact.graph, []

    def add(check: str, target: str, why: str) -> None:
        if check in enabled:
            items.append(PlanItem(check, target, why))

    referenced = {t for r in impact.refs.values() for t in r.columns}
    for t in g.tables_with("reads"):
        meta = ev.facts.get(f"ev:uc:{t}:metadata", {})
        add("contract.unknown-object", t, "table read by an impacted task")
        if t in referenced and meta.get("exists"):
            add("contract.column-mismatch", t, "code references columns on this table")
        tags = ev.facts.get(f"ev:uc:{t}:tags", {})
        if tags.get("column_tags"):
            add("governance.tagged-data-flow", t, "source has governance-tagged columns")
        status = " ".join(f"{k}={v}" for k, v in tags.get("table_tags", {}).items()).lower()
        if "deprecated" in status or "deprecated" in (meta.get("comment") or "").lower():
            add("semantic.non-authoritative-source", t, "source is marked deprecated")
    if any(r.joins for r in impact.refs.values()):
        add("contract.type-mismatch", "joins", "code joins tables on columns")
    for t in g.tables_with("writes"):
        n = g.nodes[f"uc:{t}"]
        if n.get("change") == "schema_changed":
            add("contract.write-schema", t, "written table's columns change")
            add("lineage.downstream-break", t, f"columns removed: {n['columns']['removed']}")
        if n.get("change") == "new_write" and any(k.endswith(":metric_view") for k in ev.facts):
            add("semantic.metric-definition-drift", t, "new output near a governed metric view")
    for nid, n in g.nodes.items():
        if n["kind"] == "job" and n.get("deployed_id"):
            add("deploy.drift", nid[4:], "job changes and is already deployed")
    if any(k.startswith("ev:workspace:spark_version:") for k in ev.facts):
        add("deploy.runtime-support", "clusters", "PR defines cluster specs")
    return items


# ------------------------------------------------------------------ deterministic checks


def _short(fqn: str) -> str:
    """Display name: drop catalog and schema (the full name appears in the evidence)."""
    parts = fqn.split(".")
    return ".".join(parts[2:]) if len(parts) > 3 else parts[-1]


def _locate(impact: ImpactResult, table: str, rel: str) -> tuple[str, int]:
    for e in impact.head.entries.values():
        if table in e[rel]:
            return e["entry"], e[rel][table]
    return "", 1


def _cols(ev: Evidence, t: str) -> dict[str, str]:
    return {c["name"].lower(): c["type"] for c in ev.facts.get(f"ev:uc:{t}:metadata", {}).get("columns", [])}


def deterministic(impact: ImpactResult, ev: Evidence, enabled: set[str]) -> list[Finding]:
    g, out = impact.graph, []
    written = set(g.tables_with("writes"))

    def add(check: str, sev: str, file: str, line: int, params: dict, evidence: list[str]) -> None:
        if check in enabled:
            w = wording.exact(check, params)
            out.append(Finding(check, sev, file, line, w["title"], w["description"], evidence, w["recommendation"],
                               impact=w["impact"], params=params))

    renamed: dict[str, dict[str, str]] = {}  # table -> {new_col: old_col} when one removed + one added
    for t in written:
        cols = g.nodes[f"uc:{t}"].get("columns") or {}
        if len(cols.get("removed", [])) == 1 and len(cols.get("added", [])) == 1:
            renamed[t] = {cols["added"][0].lower(): cols["removed"][0].lower()}

    # unknown objects
    for t in g.tables_with("reads"):
        meta = ev.facts.get(f"ev:uc:{t}:metadata", {})
        if meta.get("exists") is False and t not in written:
            f, line = _locate(impact, t, "reads")
            add("contract.unknown-object", "critical", f, line, {"table": t}, [f"ev:uc:{t}:metadata"])

    # missing columns
    for key, refs in impact.refs.items():
        entry = impact.head.entries[key]["entry"]
        for t, cols in refs.columns.items():
            live = _cols(ev, t)
            if not live:
                continue
            added = {c.lower() for c in (g.nodes.get(f"uc:{t}", {}).get("columns") or {}).get("added", [])}
            for c, line in cols.items():
                if c.lower() not in live and c.lower() not in added:
                    add("contract.column-mismatch", "high", entry, line, {"table": t, "column": c, "live": sorted(live)},
                        [f"ev:uc:{t}:metadata"])

    # join type mismatches (uses the old column's type for a column renamed in this PR)
    def col_type(t: str, c: str) -> str | None:
        live = _cols(ev, t)
        c = c.lower()
        return live.get(c) or live.get(renamed.get(t, {}).get(c, ""))
    for key, refs in impact.refs.items():
        entry = impact.head.entries[key]["entry"]
        for j in refs.joins:
            (lt, lc), (rt, rc) = j["left"], j["right"]
            ltype, rtype = col_type(lt, lc), col_type(rt, rc)
            if not ltype or not rtype:
                continue
            lf, rf = TYPE_FAMILY.get(ltype.split("(")[0].lower()), TYPE_FAMILY.get(rtype.split("(")[0].lower())
            if lf and rf and lf != rf:
                add("contract.type-mismatch", "medium", entry, j["line"], {"left": [lt, lc, ltype], "right": [rt, rc, rtype]},
                    [f"ev:uc:{lt}:metadata", f"ev:uc:{rt}:metadata"])

    # schema changes and downstream breaks
    for t in written:
        n = g.nodes[f"uc:{t}"]
        meta = ev.facts.get(f"ev:uc:{t}:metadata", {})
        if n.get("change") != "schema_changed" or not meta.get("exists"):
            continue
        removed = n["columns"]["removed"]
        file, line = _schema_change_location(impact, t, n["columns"]["added"] + removed)
        add("contract.write-schema", "high", file, line, {"table": t, "removed": removed, "added": n["columns"]["added"]},
            [f"ev:uc:{t}:metadata"])
        broken = []
        for c in removed:
            broken += ev.facts.get(f"ev:lineage:{t}.{c}:downstream", {}).get("downstream_columns", [])
        if broken:
            add("lineage.downstream-break", "critical", file, line, {"table": t, "removed": removed, "consumers": broken},
                [f"ev:lineage:{t}.{c}:downstream" for c in removed] + [f"ev:lineage:{t}:downstream"])

    # runtime support
    for k, fact in ev.facts.items():
        if k.startswith("ev:workspace:spark_version:") and fact.get("offered_by_workspace") is False:
            sv = k.rsplit(":", 1)[1]
            file, line = _find_in_changed(impact, sv)
            add("deploy.runtime-support", "high", file, line, {"version": sv, "offered": fact.get("examples_offered", [])}, [k])

    # drift
    for k, fact in ev.facts.items():
        if k.startswith("ev:job:") and k.endswith(":drift") and fact.get("drift"):
            file, line = _find_in_changed(impact, fact["job_key"] + ":")
            add("deploy.drift", "medium", file, line, {"job": fact["job_key"], "fields": sorted(fact["drift"])}, [k])
    return out


def _schema_change_location(impact: ImpactResult, table: str, cols: list[str]) -> tuple[str, int]:
    for e in impact.head.entries.values():
        if table not in e["writes"]:
            continue
        for f in e["executes"]:
            if f in impact.changed_files and f.endswith(".py"):
                text = (impact_repo(impact) / f).read_text(errors="replace").splitlines()
                # Prefer the quoted column name (an alias or select), then any mention
                for match in (lambda line: any(f'"{c}"' in line or f"'{c}'" in line for c in cols),
                              lambda line: any(c in line for c in cols)):
                    for i, line in enumerate(text, 1):
                        if match(line):
                            return f, i
        return e["entry"], e["writes"][table]
    return "", 1


def _find_in_changed(impact: ImpactResult, needle: str) -> tuple[str, int]:
    for f in impact.changed_files:
        p = impact_repo(impact) / f
        if p.exists() and p.suffix in (".yml", ".yaml", ".py", ".sql"):
            for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                if needle in line:
                    return f, i
    return next(iter(impact.changed_files), ""), 1


def impact_repo(impact: ImpactResult):
    return impact.repo  # set by the pipeline


GENIE_CHECKS = {"semantic.metric-definition-drift", "semantic.non-authoritative-source", "governance.tagged-data-flow"}


def coverage(plan_items: list[PlanItem], findings: list[Finding], enabled: set[str], ev: Evidence,
             genie_ran: bool = True) -> list[dict[str, Any]]:
    """Per-check status: N finding(s) | no issues found | could not run | insufficient evidence | not relevant | not enabled.

    "No issues found" is only claimed when the check ran with the evidence it needed.
    """
    planned: dict[str, list[str]] = {}
    for p in plan_items:
        planned.setdefault(p.check, []).append(p.target)
    rows = []
    for cid in CATALOG:
        n = sum(1 for f in findings if f.check == cid)
        targets = planned.get(cid, [])
        unreadable = [t for t in targets if ev.facts.get(f"ev:uc:{t}:metadata", {}).get("access") is not None]
        if cid not in enabled:
            status = "not enabled"
        elif n:
            status = f"{n} finding{'s' if n != 1 else ''}"
        elif not targets:
            status = "not relevant to this PR"
        elif cid in GENIE_CHECKS and not genie_ran:
            status = "could not run"
        elif unreadable and len(unreadable) == len(targets):
            status = "insufficient evidence"
        else:
            status = "no issues found" + (" (some objects unreadable)" if unreadable else "")
        rows.append({"check": cid, "status": status})
    return rows

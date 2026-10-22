"""Check catalog, planner, and deterministic checks.

Every check passes the admission test: it needs a fact that lives in Databricks, not in the repo.
Deterministic checks run without any model; Genie reviews the planned checks on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .evidence import Evidence
from .impact import ImpactResult
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

    def add(check: str, sev: str, file: str, line: int, title: str, expl: str, evidence: list[str], sugg: str = "") -> None:
        if check in enabled:
            out.append(Finding(check, sev, file, line, title, expl, evidence, sugg))

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
            add("contract.unknown-object", "critical", f, line, f"`{t}` does not exist",
                f"The code reads `{t}`, which does not exist in the target workspace and is not created by this PR.",
                [f"ev:uc:{t}:metadata"], "Create the table first, fix the name, or add the task that produces it.")

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
                    add("contract.column-mismatch", "high", entry, line, f"`{t.split('.')[-1]}.{c}` does not exist",
                        f"`{t}` has no column `{c}`. Live columns: {', '.join(sorted(live))}.",
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
                add("contract.type-mismatch", "medium", entry, j["line"],
                    f"Join compares {ltype} `{lc}` with {rtype} `{rc}`",
                    f"`{lt}.{lc}` is {ltype} and `{rt}.{rc}` is {rtype}. The join relies on an implicit cast, which is slow and can silently drop or mismatch rows.",
                    [f"ev:uc:{lt}:metadata", f"ev:uc:{rt}:metadata"],
                    "Cast explicitly to a common type, or align the column types at the source.")

    # schema changes and downstream breaks
    for t in written:
        n = g.nodes[f"uc:{t}"]
        meta = ev.facts.get(f"ev:uc:{t}:metadata", {})
        if n.get("change") != "schema_changed" or not meta.get("exists"):
            continue
        removed = n["columns"]["removed"]
        file, line = _schema_change_location(impact, t, n["columns"]["added"] + removed)
        add("contract.write-schema", "high", file, line, f"Write changes the schema of existing table `{t.split('.')[-1]}`",
            f"`{t}` currently has {', '.join(removed)}; this PR stops writing {', '.join(removed)} and adds {', '.join(n['columns']['added']) or 'nothing'}.",
            [f"ev:uc:{t}:metadata"], "Plan the migration: keep the old column during a transition, or update consumers in the same change.")
        broken = []
        for c in removed:
            broken += ev.facts.get(f"ev:lineage:{t}.{c}:downstream", {}).get("downstream_columns", [])
        if broken:
            add("lineage.downstream-break", "critical", file, line,
                f"Removing `{', '.join(removed)}` breaks {len(broken)} downstream column(s)",
                f"Column lineage shows these consumers of `{t}.{', '.join(removed)}`: {', '.join(broken)}.",
                [f"ev:lineage:{t}.{c}:downstream" for c in removed] + [f"ev:lineage:{t}:downstream"],
                "Update or migrate the downstream objects before (or with) this change.")

    # runtime support
    for k, fact in ev.facts.items():
        if k.startswith("ev:workspace:spark_version:") and fact.get("offered_by_workspace") is False:
            sv = k.rsplit(":", 1)[1]
            file, line = _find_in_changed(impact, sv)
            add("deploy.runtime-support", "high", file, line, f"Runtime `{sv}` is not offered by this workspace",
                f"The workspace does not offer `{sv}`. Examples it offers: {', '.join(fact.get('examples_offered', []))}.",
                [k], "Use a supported LTS runtime.")

    # drift
    for k, fact in ev.facts.items():
        if k.startswith("ev:job:") and k.endswith(":drift") and fact.get("drift"):
            file, line = _find_in_changed(impact, fact["job_key"] + ":")
            add("deploy.drift", "medium", file, line, f"Deploy will overwrite UI changes on job `{fact['job_key']}`",
                f"The deployed job differs from the bundle on the base branch in: {', '.join(fact['drift'])}. Deploying this PR replaces those settings.",
                [k], "Bring the out-of-band changes into the bundle, or confirm they should be reverted.")
    return out


def _schema_change_location(impact: ImpactResult, table: str, cols: list[str]) -> tuple[str, int]:
    for e in impact.head.entries.values():
        if table not in e["writes"]:
            continue
        for f in e["executes"]:
            if f in impact.changed_files and f.endswith(".py"):
                text = (impact_repo(impact) / f).read_text(errors="replace").splitlines()
                for i, line in enumerate(text, 1):
                    if any(c in line for c in cols):
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


def coverage(plan_items: list[PlanItem], findings: list[Finding], enabled: set[str], gaps: list[str]) -> list[dict[str, Any]]:
    planned = {p.check for p in plan_items}
    rows = []
    for cid in CATALOG:
        n = sum(1 for f in findings if f.check == cid)
        if cid not in enabled:
            status = "not enabled"
        elif cid not in planned:
            status = "not relevant to this PR"
        else:
            status = f"{n} finding(s)" if n else "passed"
        rows.append({"check": cid, "status": status})
    return rows

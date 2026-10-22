"""Which analysis capabilities ran, which did not, and how many findings each contributed to.

This replaces a list of triggered rules: it answers "what did the review actually look at?".
A finding counts toward every capability it used, so the column does not sum to the issue count.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .models import Finding

RAN, NOT_NEEDED, UNAVAILABLE, SKIPPED = "Ran", "Not needed", "Unavailable", "Skipped"


@dataclass
class Capability:
    name: str
    status: str
    detail: str
    findings: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _uses(findings: list[Finding], label: str) -> int:
    return sum(1 for f in findings if label in f.detected_by)


def build(graph: dict[str, Any], facts: dict[str, Any], findings: list[Finding], gaps: list[str],
          genie_code: str, genie_one: dict[str, Any] | None = None) -> list[Capability]:
    """`genie_code`: ran | unavailable | not needed. `genie_one`: ontology stage summary (see ontology.py)."""
    nodes = graph.get("nodes", [])
    files = sum(1 for n in nodes if n["kind"] == "file")
    tasks = sum(1 for n in nodes if n["kind"] in ("task", "pipeline"))
    tables = [k for k in facts if k.startswith("ev:uc:") and k.endswith(":metadata")]
    unreadable = sum(1 for k in tables if facts[k].get("exists") is None)
    col_lin = [k for k in facts if k.startswith("ev:lineage:") and k[len("ev:lineage:"):].rsplit(":", 1)[0].count(".") == 3]
    tab_lin = [k for k in facts if k.startswith("ev:lineage:") and k not in col_lin]
    tagged = [k for k in facts if k.endswith(":tags")]
    metric_views = [k for k in facts if k.endswith(":metric_view")]
    jobs = [k for k in facts if k.startswith("ev:job:")]
    runtimes = [k for k in facts if k.startswith("ev:workspace:spark_version:")]
    gap_text = " ".join(gaps).lower()

    def status(present: bool, gap_words: tuple[str, ...]) -> str:
        if any(w in gap_text for w in gap_words):
            return UNAVAILABLE if not present else RAN
        return RAN if present else NOT_NEEDED

    caps = [
        Capability("Bundle and code analysis", RAN, f"{files} changed file(s), {tasks} affected task(s)",
                   sum(1 for f in findings if f.source == "deterministic")),
        Capability("Unity Catalog metadata", status(bool(tables), ("metadata",)),
                   f"{len(tables)} table(s) checked" + (f", {unreadable} unreadable" if unreadable else ""), _uses(findings, "Unity Catalog")),
        Capability("Lineage", status(bool(col_lin or tab_lin), ("lineage",)),
                   f"{len(tab_lin)} table(s), {len(col_lin)} changed column(s)", _uses(findings, "Lineage")),
        Capability("Governance tags and metric views", status(bool(tagged or metric_views), ("warehouse", "tags")),
                   f"{len(tagged)} tagged object(s), {len(metric_views)} metric view(s)",
                   sum(1 for f in findings if any(e.endswith((":tags", ":metric_view")) for e in f.evidence))),
        Capability("Deployed job state", status(bool(jobs), ("deployed",)), f"{len(jobs)} deployed job(s) compared", _uses(findings, "Deployed job")),
        Capability("Workspace runtimes", status(bool(runtimes), ("runtime",)), f"{len(runtimes)} runtime version(s) checked", _uses(findings, "Workspace")),
        Capability("Genie Code review", {"ran": RAN, "unavailable": UNAVAILABLE}.get(genie_code, NOT_NEEDED),
                   "AI review of the planned checks" if genie_code == "ran" else "no Genie Code model reachable" if genie_code == "unavailable" else "no checks needed AI review",
                   sum(1 for f in findings if f.source == "genie")),
    ]
    g1 = genie_one or {}
    caps.append(Capability("Genie One (ontology)", g1.get("status", NOT_NEEDED), g1.get("detail", "not called"), _uses(findings, "Genie One")))
    return caps

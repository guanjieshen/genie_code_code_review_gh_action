"""Core data structures: the impact graph, evidence, plan items, and findings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SEVERITIES = ["critical", "high", "medium", "low", "info"]


@dataclass
class ImpactGraph:
    """What a PR touches. Nodes are files, resources, and data objects; edges record how each link was found."""

    target: str
    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[dict[str, str]] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    job_diff: dict[str, Any] = field(default_factory=dict)

    def node(self, node_id: str, kind: str, **attrs: Any) -> dict[str, Any]:
        n = self.nodes.setdefault(node_id, {"id": node_id, "kind": kind})
        n.update({k: v for k, v in attrs.items() if v is not None})
        return n

    def edge(self, src: str, dst: str, rel: str, how: str, confidence: str = "resolved") -> None:
        e = {"from": src, "to": dst, "rel": rel, "how": how, "confidence": confidence}
        if e not in self.edges:
            self.edges.append(e)

    def tables(self) -> list[str]:
        return [n["id"][3:] for n in self.nodes.values() if n["kind"] == "table"]

    def tables_with(self, rel: str) -> list[str]:
        return sorted({e["to"][3:] for e in self.edges if e["rel"] == rel and e["to"].startswith("uc:")})

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "nodes": list(self.nodes.values()),
            "edges": self.edges,
            "unresolved": self.unresolved,
            "job_diff": self.job_diff,
        }


@dataclass
class PlanItem:
    check: str
    target: str
    why: str


CONFIDENCE = ["confirmed", "high", "medium", "low"]
MERGE_IMPACT = ["blocker", "action_required", "review_recommended", "informational"]
CATEGORIES = {  # stable id -> display name, in default display order
    "contracts": "Data contracts & compatibility",
    "correctness": "Data correctness & quality",
    "semantics": "Business semantics & metrics",
    "lineage": "Lineage, relationships & ontology",
    "governance": "Security & governance",
    "deployment": "Deployment & operations",
    "other": "Other",
}


@dataclass
class Finding:
    """One check result. Legacy fields (check, file, line, explanation, suggestion, source) are kept for
    backward compatibility; normalized fields are filled in by normalize.py."""

    check: str                      # rule id, e.g. contract.unknown-object
    severity: str                   # critical | high | medium | low | info
    file: str
    line: int
    title: str
    explanation: str                # description: what is wrong
    evidence: list[str]             # evidence ids (ev:...) or genie_one:<question>
    suggestion: str = ""            # recommendation
    model_confidence: float = 1.0   # Genie's self-reported score; only used to downgrade
    source: str = "deterministic"   # deterministic | genie
    impact: str = ""                # consequence, in business terms where possible
    affected_objects: list[str] = field(default_factory=list)
    # ---- normalized (normalize.py / correlate.py)
    id: str = ""
    category: str = "other"
    confidence: str = "medium"      # confirmed | high | medium | low
    merge_impact: str = "review_recommended"
    detected_by: list[str] = field(default_factory=list)
    group_id: str = ""
    related_findings: list[str] = field(default_factory=list)

    @property
    def rule_id(self) -> str:
        return self.check

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(rule_id=self.check, description=self.explanation, recommendation=self.suggestion,
                 location={"file": self.file, "line": self.line})
        return d

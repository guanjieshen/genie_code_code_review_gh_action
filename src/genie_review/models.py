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


@dataclass
class Finding:
    check: str
    severity: str
    file: str
    line: int
    title: str
    explanation: str
    evidence: list[str]
    suggestion: str = ""
    confidence: float = 1.0
    source: str = "deterministic"  # deterministic | genie

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.check, self.file, self.title.lower()[:60])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

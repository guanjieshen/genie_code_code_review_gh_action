"""Correlation and prioritization.

Grouping is rule-based and conservative: findings are grouped only when they come from the same
underlying change and share a fix. Every original finding (and its evidence) is kept.

Ordering is deterministic and explainable. Issues are sorted by, in order:
  1. merge impact (blocker > action required > review recommended > informational)
  2. severity
  3. governed asset at stake (metric view or certified/deprecated dataset) with high or confirmed confidence
  4. confidence (confirmed failures before possible risks)
  5. downstream blast radius (number of downstream objects in lineage)
  6. category display order, then file and line (stable tie-break)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from .models import CATEGORIES, CONFIDENCE, MERGE_IMPACT, SEVERITIES, Finding


@dataclass
class Group:
    """One displayed issue. Usually one finding; sometimes several checks of the same root cause."""

    id: str
    key: str
    findings: list[Finding]
    governed: bool = False
    blast_radius: int = 0
    reason: str = ""  # why it is ranked where it is

    @property
    def primary(self) -> Finding:
        return self.findings[0]

    @property
    def severity(self) -> str:
        return min((f.severity for f in self.findings), key=SEVERITIES.index)

    @property
    def confidence(self) -> str:
        return min((f.confidence for f in self.findings), key=CONFIDENCE.index)

    @property
    def merge_impact(self) -> str:
        return min((f.merge_impact for f in self.findings), key=MERGE_IMPACT.index)

    @property
    def category(self) -> str:
        return self.primary.category

    @property
    def checks(self) -> list[str]:
        return sorted({f.check for f in self.findings})

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "key": self.key, "category": self.category, "severity": self.severity,
                "confidence": self.confidence, "merge_impact": self.merge_impact, "title": self.primary.title,
                "findings": [f.id for f in self.findings], "checks": self.checks, "governed": self.governed,
                "blast_radius": self.blast_radius, "priority_reason": self.reason}


def _deprecated(facts: dict[str, Any], obj: str) -> bool:
    fqn = obj[3:]
    tags = (facts.get(f"ev:uc:{fqn}:tags") or {}).get("table_tags") or {}
    comment = ((facts.get(f"ev:uc:{fqn}:metadata") or {}).get("comment") or "").lower()
    return "deprecated" in " ".join(f"{k}={v}" for k, v in tags.items()).lower() or comment.startswith("deprecated")


def root_cause_key(f: Finding, facts: dict[str, Any]) -> str:
    """A key shared only by findings that come from the same underlying change."""
    tables = [o for o in f.affected_objects if o.startswith("uc:")]
    if f.check in ("contract.write-schema", "lineage.downstream-break") and f.source == "deterministic":
        written = sorted(o for o in tables if facts.get(f"ev:uc:{o[3:]}:metadata", {}).get("exists"))
        if written:
            return f"schema-change:{written[0]}:{f.file}"
    if f.check.startswith("semantic."):
        dep = sorted(o for o in tables if _deprecated(facts, o))
        if dep:
            return f"deprecated-source:{dep[0]}:{f.file}"
    return f"finding:{f.id}"


def _governed(fs: list[Finding], facts: dict[str, Any]) -> bool:
    """Does the issue involve a governed data product (metric view, certified or deprecated dataset)?"""
    for e in {e for f in fs for e in f.evidence}:
        if e.endswith(":metric_view"):
            return True
        if e.endswith(":tags"):
            tags = (facts.get(e) or {}).get("table_tags") or {}
            if any(k in ("certified", "deprecated") for k in tags):
                return True
    return False


def _blast_radius(fs: list[Finding], facts: dict[str, Any]) -> int:
    n = 0
    for e in {e for f in fs for e in f.evidence if e.startswith("ev:lineage:")}:
        fact = facts.get(e) or {}
        n += len(fact.get("downstream_columns") or []) + len(fact.get("downstream") or [])
    return n


def _sort_key(g: Group) -> tuple:
    governed_confident = g.governed and CONFIDENCE.index(g.confidence) <= CONFIDENCE.index("high")
    p = g.primary
    return (MERGE_IMPACT.index(g.merge_impact), SEVERITIES.index(g.severity), 0 if governed_confident else 1,
            CONFIDENCE.index(g.confidence), -g.blast_radius, list(CATEGORIES).index(g.category), p.file, p.line)


def _reason(g: Group) -> str:
    bits = [g.severity, f"{g.confidence} confidence"]
    if g.governed:
        bits.append("governed data product")
    if g.blast_radius:
        bits.append(f"{g.blast_radius} downstream object(s)")
    return ", ".join(bits)


def _member_order(f: Finding) -> tuple:
    # In a group, show the most severe, best-evidenced check first; prefer exact checks on ties.
    return (SEVERITIES.index(f.severity), CONFIDENCE.index(f.confidence), 0 if f.source == "deterministic" else 1, f.check)


def correlate(findings: list[Finding], facts: dict[str, Any], group_related: bool = True) -> list[Group]:
    buckets: dict[str, list[Finding]] = {}
    for f in findings:
        key = root_cause_key(f, facts) if group_related else f"finding:{f.id}"
        buckets.setdefault(key, []).append(f)
    groups = []
    for key, fs in buckets.items():
        fs.sort(key=_member_order)
        gid = "g-" + hashlib.sha1(key.encode()).hexdigest()[:10]
        for f in fs:
            f.group_id = gid
            f.related_findings = [o.id for o in fs if o is not f]
        g = Group(gid, key, fs, _governed(fs, facts), _blast_radius(fs, facts))
        g.reason = _reason(g)
        groups.append(g)
    groups.sort(key=_sort_key)
    return groups


def needs_attention(groups: list[Group], limit: int, min_confidence: str) -> list[Group]:
    """Top issues shown fully at the top of the report: blockers and required actions with enough evidence."""
    picked = [g for g in groups if g.merge_impact in ("blocker", "action_required")
              and CONFIDENCE.index(g.confidence) <= CONFIDENCE.index(min_confidence)]
    return picked[:limit]

"""Normalization: category, confidence, merge impact, affected objects, and stable ids.

Confidence comes from the quality of the evidence a finding cites, never from a model's own score.
Evidence hierarchy (strongest first):
  1. deterministic code/config analysis   2. verified workspace metadata
  3. explicit lineage, policies, metric definitions   4. documented metadata (comments, tags)
  5. Genie-assisted interpretation   6. heuristics
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from . import wording
from .models import CATEGORIES, CONFIDENCE, MERGE_IMPACT, SEVERITIES, Finding

# Stable rule -> category mapping. New rules default to "other".
RULE_CATEGORY = {
    "contract.unknown-object": "contracts",
    "contract.column-mismatch": "contracts",
    "contract.type-mismatch": "contracts",
    "contract.write-schema": "contracts",
    "lineage.downstream-break": "lineage",
    "ontology.identifier-relationship": "lineage",
    "correctness.aggregation-grain": "correctness",
    "semantic.filter-meaning": "semantics",
    "semantic.metric-definition-drift": "semantics",
    "semantic.non-authoritative-source": "semantics",
    "governance.tagged-data-flow": "governance",
    "deploy.drift": "deployment",
    "deploy.runtime-support": "deployment",
}

# Plain-language consequence for exact checks (Genie supplies its own).
# Rules whose findings rest on a business definition from Genie One (ontology). They need an authoritative answer.
ONTOLOGY_RULES = {"ontology.identifier-relationship", "correctness.aggregation-grain", "semantic.filter-meaning"}

RULE_IMPACT = {
    "contract.unknown-object": "The task fails at runtime when it reads the missing object.",
    "contract.column-mismatch": "The query fails at runtime with an unresolved column error.",
    "contract.type-mismatch": "Spark casts one side implicitly; values that do not convert become NULL and do not match.",
    "contract.write-schema": "The write changes an existing table's columns, so readers of the old columns break.",
    "lineage.downstream-break": "Downstream tables and views that read the removed column fail or return wrong results after deploy.",
    "deploy.drift": "Deploying this change silently reverts settings someone changed on the live job.",
    "deploy.runtime-support": "The job cannot start a cluster with this runtime in the target workspace.",
}


@dataclass
class Policy:
    """Merge-impact policy. Defaults are conservative: only confirmed, severe, deterministic-grade issues block."""

    block_severities: list[str] = field(default_factory=lambda: ["critical"])
    block_min_confidence: str = "confirmed"
    block_categories: list[str] = field(default_factory=lambda: ["contracts", "lineage", "deployment"])


def _rank(scale: list[str], v: str) -> int:
    return scale.index(v) if v in scale else len(scale)


def objects_from_evidence(evidence: list[str]) -> list[str]:
    """Normalized affected objects: uc:<fqn>, job:<id>, runtime:<version>."""
    out = []
    for e in evidence:
        m = re.match(r"ev:uc:([^:]+):", e) or re.match(r"ev:lineage:([^:]+):", e)
        if m:
            fqn = m.group(1)
            parts = fqn.split(".")
            out.append("uc:" + ".".join(parts[:3]))
        elif e.startswith("ev:job:"):
            out.append("job:" + e.split(":")[2])
        elif e.startswith("ev:workspace:spark_version:"):
            out.append("runtime:" + e.rsplit(":", 1)[1])
    return sorted(set(out))


def _fact_kinds(f: Finding, facts: dict[str, Any]) -> set[str]:
    """What kind of evidence a finding stands on."""
    kinds = set()
    for e in f.evidence:
        if e.startswith("genie_one:"):
            kinds.add("genie_one")
        elif e.startswith("ev:genie_one:"):
            g1 = facts.get(e) or {}
            if g1.get("status") == "answered":
                kinds.add("genie_one")
                kinds.add("genie_one_authoritative" if g1.get("authoritative") else "genie_one_hypothesis")
                if g1.get("queries"):
                    kinds.add("genie_one_data")   # Genie One ran queries against the reviewed tables
                if g1.get("conflicting"):
                    kinds.add("genie_one_conflict")
        elif e.endswith(":metric_view"):
            kinds.add("metric_view")
        elif e.endswith(":tags"):
            tags = facts.get(e, {})
            status = " ".join(f"{k}={v}" for k, v in (tags.get("table_tags") or {}).items()).lower()
            kinds.add("governed_tag" if ("certified" in status or "deprecated" in status) else "tags")
            if tags.get("column_tags"):
                kinds.add("column_tags")
        elif ":lineage:" in e or e.startswith("ev:lineage:"):
            kinds.add("column_lineage" if e.count(".") >= 3 and e.endswith(":downstream") and _is_column(e) else "table_lineage")
        elif e.endswith(":metadata"):
            meta = facts.get(e, {})
            kinds.add("metadata" if meta.get("exists") is not None else "metadata_unverified")
        elif e.startswith("ev:job:"):
            kinds.add("deployed_state")
        elif e.startswith("ev:workspace:"):
            kinds.add("workspace")
    return kinds


def _is_column(e: str) -> bool:
    name = e[len("ev:lineage:"):].rsplit(":", 1)[0]
    return name.count(".") == 3


def assess_confidence(f: Finding, facts: dict[str, Any]) -> str:
    kinds = _fact_kinds(f, facts)
    if "metadata_unverified" in kinds and not (kinds - {"metadata_unverified"}):
        return "low"  # nothing verified
    if f.source == "deterministic":
        if f.check == "lineage.downstream-break":
            return "confirmed" if "column_lineage" in kinds else "high"
        if f.check == "contract.type-mismatch":
            return "high"  # types verified; the effect on rows depends on the data
        return "confirmed" if kinds & {"metadata", "deployed_state", "workspace", "column_lineage"} else "high"
    # Genie-derived: confidence depends on what it is anchored to
    if f.check in ONTOLOGY_RULES:
        # needs an authoritative business definition; an unsourced answer stays a hypothesis
        if "genie_one_authoritative" not in kinds:
            level = "low"
        else:
            level = "high" if kinds & {"metadata", "genie_one_data"} else "medium"
        if "genie_one_conflict" in kinds:
            level = "medium" if level == "high" else level
    elif f.check.startswith("semantic."):
        level = "high" if kinds & {"metric_view", "governed_tag", "genie_one_authoritative"} else "medium"
    elif f.check == "governance.tagged-data-flow":
        level = "medium"  # tags show classification; effective permissions and masks are not inspected
    else:
        level = "high" if kinds & {"metadata", "column_lineage", "deployed_state", "workspace"} else "medium"
    if f.model_confidence < 0.5:  # the model's own doubt can only lower confidence
        level = CONFIDENCE[min(_rank(CONFIDENCE, level) + 1, len(CONFIDENCE) - 1)]
    return level


def merge_impact(f: Finding, policy: Policy) -> str:
    sev, conf = _rank(SEVERITIES, f.severity), _rank(CONFIDENCE, f.confidence)
    if (f.severity in policy.block_severities and conf <= _rank(CONFIDENCE, policy.block_min_confidence)
            and f.category in policy.block_categories):
        return "blocker"
    if sev <= _rank(SEVERITIES, "high") and conf <= _rank(CONFIDENCE, "high"):
        return "action_required"
    if sev <= _rank(SEVERITIES, "medium") and conf <= _rank(CONFIDENCE, "medium"):
        return "review_recommended"
    return "informational"


def detected_by(f: Finding, facts: dict[str, Any]) -> list[str]:
    """Which capabilities actually produced or supported the finding.

    "Genie Code" is the AI review engine; "Genie One" is listed only when the finding cites a Genie One
    question that was asked. Exact checks are labelled with the data source they used.
    """
    kinds = _fact_kinds(f, facts)
    labels = ["Genie Code"] if f.source == "genie" else ["Exact check"]
    if "genie_one" in kinds:
        labels.append("Genie One")
    if kinds & {"metadata", "tags", "governed_tag", "column_tags", "metric_view"}:
        labels.append("Unity Catalog")
    if kinds & {"column_lineage", "table_lineage"}:
        labels.append("Lineage")
    if "deployed_state" in kinds:
        labels.append("Deployed job")
    if "workspace" in kinds:
        labels.append("Workspace")
    return labels


def finding_id(f: Finding) -> str:
    key = "|".join([f.check, f.file, str(f.line), ",".join(f.affected_objects), f.title.lower()[:40]])
    return "f-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def _specific_title(f: Finding) -> None:
    """Titles must say what is wrong. Very short model titles get the rule's description as a lead-in."""
    from .checks import CATALOG
    words = re.sub(r"`[^`]*`", "x", f.title).split()
    if len(words) < 4 and f.check in CATALOG:
        f.title = f"{CATALOG[f.check].title}: {f.title}"


def _apply_wording(f: Finding, facts: dict[str, Any]) -> None:
    """Exact checks are worded from their params; every actionable finding gets a fix; no repeated impact."""
    if f.source == "deterministic" and f.params:
        w = wording.exact(f.check, f.params)
        if w:
            f.title, f.explanation, f.impact, f.suggestion = w["title"], w["description"], w["impact"], w["recommendation"]
    if f.source == "genie":
        _specific_title(f)
    if not f.suggestion:
        f.suggestion = wording.DEFAULT_RECOMMENDATION.get(f.check, "")
    if f.impact and wording.overlaps(f.explanation, f.impact):
        f.impact = ""
    anchored = bool(_fact_kinds(f, facts) & {"metric_view", "governed_tag", "genie_one_authoritative"})
    f.validation = f.validation or wording.validation_note(f.check, f.confidence, f.params, anchored)


def normalize(findings: list[Finding], facts: dict[str, Any], policy: Policy) -> list[Finding]:
    for f in findings:
        f.category = RULE_CATEGORY.get(f.check, "other")
        f.affected_objects = f.affected_objects or objects_from_evidence(f.evidence)
        f.confidence = assess_confidence(f, facts)
        if f.source == "genie" and f.category == "semantics" and f.severity == "critical":
            f.severity = "high"  # semantic interpretation alone is not grounds for critical
        f.impact = f.impact or RULE_IMPACT.get(f.check, "")
        _apply_wording(f, facts)
        f.detected_by = detected_by(f, facts)
        f.merge_impact = merge_impact(f, policy)
        f.id = finding_id(f)
    return findings


def visible(f: Finding, categories: list[str], min_severity: str, show_low_confidence: bool, include_info: bool) -> bool:
    """Whether a finding appears in the PR comment (all findings stay in the JSON output)."""
    if f.category not in categories and f.category != "other":
        return False
    if _rank(SEVERITIES, f.severity) > _rank(SEVERITIES, min_severity):
        return False
    if f.confidence == "low" and not show_low_confidence:
        return False
    if f.severity == "info" and not include_info:
        return False
    return True


__all__ = ["CATEGORIES", "MERGE_IMPACT", "Policy", "normalize", "visible"]

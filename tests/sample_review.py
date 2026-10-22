"""Build a normalized review from the 12-finding regression fixture (a real run on the demo PR)."""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from genie_review.correlate import correlate, needs_attention
from genie_review.models import Finding
from genie_review.normalize import Policy, normalize, visible
from genie_review.render import LinkContext, Review, markdown

FIXTURE = Path(__file__).parent / "fixtures" / "sample_review"
FIELDS = {f.name for f in fields(Finding)}
CATEGORIES = ["contracts", "correctness", "semantics", "lineage", "governance", "deployment"]


def load() -> tuple[list[Finding], dict, str]:
    raw = json.loads((FIXTURE / "findings.json").read_text())
    findings = []
    for d in raw:
        d = dict(d)
        d["model_confidence"] = d.pop("confidence")  # v0.1 JSON stored the model score as `confidence`
        findings.append(Finding(**{k: v for k, v in d.items() if k in FIELDS}))
    facts = json.loads((FIXTURE / "evidence.json").read_text())
    summary = json.loads((FIXTURE / "genie_output.json").read_text()).get("impact_summary", "")
    return findings, facts, summary


def build(findings=None, facts=None, summary=None, group_related=True, max_priority=3, ctx=None) -> tuple[Review, str]:
    f0, x0, s0 = load()
    findings = f0 if findings is None else findings
    facts = x0 if facts is None else facts
    summary = s0 if summary is None else summary
    normalize(findings, facts, Policy())
    shown = [f for f in findings if visible(f, CATEGORIES, "low", False, False)]
    groups = correlate(shown, facts, group_related)
    review = Review(summary, groups, needs_attention(groups, max_priority, "high"), findings,
                    hidden=len(findings) - len(shown), route_note="Reviewer: Genie Code `gpt-5.6-sol` via target workspace.")
    ctx = ctx or LinkContext("https://github.com", "acme/data-platform", "0123abc", ".")
    return review, markdown(review, ctx)


if __name__ == "__main__":
    print(build()[1])

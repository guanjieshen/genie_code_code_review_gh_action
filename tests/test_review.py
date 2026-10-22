"""Normalization, policy, grouping, ordering, and rendering on the 12-finding regression fixture."""

import json
import re

from genie_review import publish
from genie_review.correlate import correlate
from genie_review.models import CATEGORIES, Finding
from genie_review.normalize import Policy, assess_confidence, merge_impact, normalize
from genie_review.render import MARKER, LinkContext, Review, markdown, status, to_json

from .sample_review import build, load


def _by_check(findings):
    out = {}
    for f in findings:
        out.setdefault(f.check, []).append(f)
    return out


# ------------------------------------------------------------------ regression fixture


def test_all_12_findings_survive_with_their_evidence():
    review, _ = build()
    assert len(review.findings) == 12
    in_groups = [f for g in review.groups for f in g.findings]
    assert len(in_groups) == 12                                    # grouping drops nothing
    raw = json.loads(open("tests/fixtures/sample_review/findings.json").read())
    assert sorted(e for f in review.findings for e in f.evidence) == sorted(e for d in raw for e in d["evidence"])


def test_severity_and_category_counts():
    review, md = build()
    assert "2 critical · 7 high · 3 medium" in md
    cats = {}
    for f in review.findings:
        cats[f.category] = cats.get(f.category, 0) + 1
    assert cats == {"lineage": 1, "contracts": 5, "semantics": 3, "deployment": 2, "governance": 1}
    # the overview counts every finding in the groups shown under a category
    rows = dict(re.findall(r"^\| ([^|]+) \| (\d+) \|", md, re.M))
    assert sum(int(v) for k, v in rows.items() if k in CATEGORIES.values()) == 12


def test_root_cause_grouping_is_conservative():
    review, md = build()
    assert "(10 issues from 12 checks)" in md
    multi = {g.key.split(":")[0]: g.checks for g in review.groups if len(g.findings) > 1}
    assert multi == {"schema-change": ["contract.write-schema", "lineage.downstream-break"],
                     "deprecated-source": ["semantic.metric-definition-drift", "semantic.non-authoritative-source"]}
    # same check, different files -> separate issues
    assert sum(1 for g in review.groups if "contract.type-mismatch" in g.checks) == 2


def test_grouped_findings_reference_each_other():
    review, _ = build()
    g = next(g for g in review.groups if g.key.startswith("schema-change"))
    a, b = g.findings
    assert a.group_id == b.group_id == g.id and a.related_findings == [b.id] and b.related_findings == [a.id]


def test_confidence_is_derived_from_evidence():
    review, _ = build()
    c = {f.check: f.confidence for f in review.findings}
    assert c["contract.unknown-object"] == "confirmed"
    assert c["lineage.downstream-break"] == "confirmed"          # column-level lineage
    assert c["contract.type-mismatch"] == "high"                 # types verified, row impact depends on data
    assert c["semantic.non-authoritative-source"] == "high"      # anchored to deprecated/certified tags
    assert c["governance.tagged-data-flow"] == "medium"          # tags only; masks/permissions not inspected


def test_merge_impact_policy():
    review, _ = build()
    m = {f.check: f.merge_impact for f in review.findings}
    assert m["contract.unknown-object"] == "blocker"
    assert m["lineage.downstream-break"] == "blocker"
    assert m["deploy.runtime-support"] == "action_required"      # high, not critical
    assert m["governance.tagged-data-flow"] == "review_recommended"
    assert m["deploy.drift"] == "review_recommended"


def test_critical_alone_is_not_a_blocker():
    f = Finding("semantic.metric-definition-drift", "critical", "a.sql", 1, "t", "e", ["genie_one:q"], source="genie")
    normalize([f], {}, Policy())
    assert f.severity == "high"                                  # semantic interpretation is capped at high
    assert f.merge_impact != "blocker"


def test_genie_attribution_is_kept():
    review, _ = build()
    semantic = [f for f in review.findings if f.category == "semantics"]
    assert semantic and all(f.source == "genie" and "Genie" in f.detected_by for f in semantic)


def test_model_score_can_only_lower_confidence():
    f = Finding("semantic.non-authoritative-source", "high", "a.py", 1, "t", "e", ["ev:uc:c.s.t:tags"], model_confidence=0.2, source="genie")
    facts = {"ev:uc:c.s.t:tags": {"table_tags": {"deprecated": "true"}, "column_tags": {}}}
    assert assess_confidence(f, facts) == "medium"               # would be high from evidence
    f.model_confidence = 1.0
    assert assess_confidence(f, facts) == "high"


def test_unverified_metadata_is_never_confirmed():
    f = Finding("contract.unknown-object", "critical", "a.sql", 1, "t", "e", ["ev:uc:c.s.t:metadata"])
    facts = {"ev:uc:c.s.t:metadata": {"exists": None, "access": "denied"}}
    normalize([f], facts, Policy())
    assert f.confidence == "low" and f.merge_impact != "blocker"


# ------------------------------------------------------------------ ordering


def test_needs_attention_puts_verified_breakage_first():
    review, _ = build()
    top = [g.key.split(":")[0] for g in review.priority]
    assert top[0] == "schema-change" and top[1] == "finding"     # blockers first
    assert review.priority[1].checks == ["contract.unknown-object"]
    # a well-supported governed revenue mismatch outranks other high-severity actions
    assert review.priority[2].checks == ["semantic.metric-definition-drift", "semantic.non-authoritative-source"]


def test_ordering_is_deterministic():
    a, md_a = build()
    findings, facts, summary = load()
    b, md_b = build(findings=list(reversed(findings)), facts=facts, summary=summary)
    assert [g.key for g in a.groups] == [g.key for g in b.groups]
    assert md_a == md_b


# ------------------------------------------------------------------ rendering


def test_layout_and_links():
    _, md = build()
    assert md.startswith(MARKER + "\n# Lakehouse Review")
    assert "**Review status:** Blocking issues found" in md
    assert md.index("## Needs attention") < md.index("## Review details")
    assert "<details><summary><b>Business semantics & metrics</b>" in md
    assert "[`src/exec_kpis.py:13`](https://github.com/acme/data-platform/blob/0123abc/src/exec_kpis.py#L13)" in md
    # evidence ids only inside the collapsed technical evidence
    first_details = md.index("<details>")
    assert "ev:" not in md[:first_details]


def test_priority_findings_are_not_repeated_in_full():
    review, md = build()
    title = review.priority[0].primary.title
    assert md.count(f"— {title}**") == 1                         # full block once
    assert "(shown in **Needs attention**)" in md


def test_bundle_dir_is_trimmed_from_display_but_not_links():
    ctx = LinkContext("https://github.com", "o/r", "abc", "team/app")
    assert ctx.link("team/app/src/x.py", 3) == "[`src/x.py:3`](https://github.com/o/r/blob/abc/team/app/src/x.py#L3)"
    assert LinkContext().link("src/x.py", 3) == "`src/x.py:3`"


def test_model_text_is_sanitized_before_rendering():
    from genie_review.genie import GenieRun
    from genie_review.models import PlanItem
    run = GenieRun(route=None, output={"impact_summary": "s", "injection_attempts": [], "findings": [{
        "check": "semantic.metric-definition-drift", "severity": "high", "file": "a.sql", "line": 1,
        "title": "Bad <img src=x onerror=1> @team", "explanation": "see ![](https://evil/?d=1)", "evidence": ["ev:uc:c.s.m:metric_view"],
        "impact": "x", "suggestion": "y", "confidence": 0.9}]})
    found, _, _ = publish.genie_findings(run, [PlanItem("semantic.metric-definition-drift", "t", "w")], {"ev:uc:c.s.m:metric_view"}, 0)
    review, md = build(findings=found, facts={"ev:uc:c.s.m:metric_view": {}}, summary="s")
    assert "<img" not in md and "evil" not in md and "@&#8203;team" in md


def test_zero_one_many_and_partial():
    _, md0 = build(findings=[], facts={}, summary="")
    assert "**Review status:** No issues found" in md0 and "## Needs attention" not in md0
    one = [load()[0][1]]
    r1, md1 = build(findings=one)
    assert "**Findings:** 1 critical" in md1 and len(r1.priority) == 1
    _, many = build()
    assert many.count("<details><summary><b>") == 5
    partial = Review("s", [], [], [], coverage=[{"check": "governance.tagged-data-flow", "status": "could not run"}])
    assert status(partial) == "No issues found in the checks that ran"


def test_json_output_keeps_legacy_and_new_fields():
    review, _ = build()
    data = to_json(review, "test")
    f = data["findings"][0]
    for key in ("check", "file", "line", "explanation", "suggestion", "source",            # v0.1 fields
                "id", "rule_id", "category", "severity", "confidence", "merge_impact",    # normalized
                "description", "impact", "recommendation", "location", "affected_objects", "detected_by",
                "evidence", "related_findings", "group_id"):
        assert key in f, key
    assert data["counts"] == {"critical": 2, "high": 7, "medium": 3, "low": 0, "info": 0}
    assert len(data["groups"]) == 10


# ------------------------------------------------------------------ publishing


def test_rerun_updates_the_existing_comment(monkeypatch, tmp_path):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7}}))
    for k, v in {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r", "GITHUB_EVENT_PATH": str(event)}.items():
        monkeypatch.setenv(k, v)
    calls = []

    class R:
        def __init__(self, data):
            self._d = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._d

    monkeypatch.setattr(publish.requests, "get", lambda url, **k: R([{"url": "https://api/c/1", "body": "x " + MARKER}]))
    monkeypatch.setattr(publish.requests, "patch", lambda url, **k: calls.append(("patch", url)) or R({"html_url": "u"}))
    monkeypatch.setattr(publish.requests, "post", lambda url, **k: calls.append(("post", url)) or R({"html_url": "u"}))
    publish.post_comment(MARKER + "\nbody")
    assert calls == [("patch", "https://api/c/1")]


def test_correlate_without_grouping_keeps_one_issue_per_finding():
    findings, facts, _ = load()
    normalize(findings, facts, Policy())
    assert len(correlate(findings, facts, group_related=False)) == 12


def test_merge_impact_respects_custom_policy():
    f = Finding("deploy.runtime-support", "high", "j.yml", 1, "t", "e", ["ev:workspace:spark_version:x"], category="deployment",
                confidence="confirmed")
    assert merge_impact(f, Policy(block_severities=["critical", "high"])) == "blocker"

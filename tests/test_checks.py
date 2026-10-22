from genie_review.checks import coverage, deterministic, enabled_checks, plan

ALL = enabled_checks("core", "genie-one", [], [])


def by_check(findings):
    out = {}
    for f in findings:
        out.setdefault(f.check, []).append(f)
    return out


def test_enabled_checks_respects_semantic_and_disable():
    assert "semantic.metric-definition-drift" in ALL
    assert "semantic.metric-definition-drift" not in enabled_checks("core", "off", [], [])
    assert "deploy.drift" not in enabled_checks("core", "genie-one", [], ["deploy.drift"])


def test_planner_only_plans_relevant_checks(scenario):
    impact, ev = scenario
    planned = {(p.check, p.target) for p in plan(impact, ev, ALL)}
    assert ("lineage.downstream-break", "main.shop.orders") in planned
    assert ("contract.column-mismatch", "main.shop.customers") in planned
    assert ("governance.tagged-data-flow", "main.shop.customers") in planned
    assert ("deploy.drift", "jobs.orders_etl") in planned
    assert not any(c == "semantic.non-authoritative-source" for c, _ in planned)  # nothing deprecated


def test_deterministic_findings(scenario):
    impact, ev = scenario
    f = by_check(deterministic(impact, ev, ALL))
    assert f["contract.unknown-object"][0].file == "src/segment.sql" and f["contract.unknown-object"][0].line == 8
    assert "customer_segment" in f["contract.column-mismatch"][0].title
    # customer_id is new in this PR; its type comes from the renamed cust_id (bigint) vs customers.cust_id (string)
    assert "bigint" in f["contract.type-mismatch"][0].title and "string" in f["contract.type-mismatch"][0].title
    assert f["lineage.downstream-break"][0].severity == "critical"
    assert f["lineage.downstream-break"][0].file == "src/common/transforms.py"
    assert f["deploy.runtime-support"][0].file == "resources/job.yml"
    assert f["deploy.drift"][0].evidence == ["ev:job:42:drift"]
    for finding in sum(f.values(), []):
        assert all(e in ev.facts for e in finding.evidence), finding.title


def test_disabled_check_produces_no_findings(scenario):
    impact, ev = scenario
    found = deterministic(impact, ev, ALL - {"deploy.drift"})
    assert not any(x.check == "deploy.drift" for x in found)


def test_coverage_reports_status_for_every_check(scenario):
    impact, ev = scenario
    p = plan(impact, ev, ALL)
    rows = {r["check"]: r["status"] for r in coverage(p, deterministic(impact, ev, ALL), ALL, [])}
    assert rows["semantic.non-authoritative-source"] == "not relevant to this PR"
    assert rows["deploy.drift"] == "1 finding(s)"

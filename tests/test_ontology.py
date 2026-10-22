"""Ontology validation: planning, Unity Catalog resolution, citations, confidence, grouping, coverage."""

from genie_review import ontology
from genie_review.correlate import correlate
from genie_review.models import Finding
from genie_review.normalize import Policy, normalize

C = "main.shop"


def test_planner_dedupes_by_concept_and_ranks_identifiers_first(scenario):
    impact, ev = scenario
    impact.refs["jobs.orders_etl.segment"].joins.append(
        {"left": [f"{C}.customer_ltv", "cust_id"], "right": [f"{C}.customers", "cust_id"], "line": 20})
    impact.head.entries["jobs.orders_etl.segment"]["outputs"] = {f"{C}.segment_revenue": ["segment", "net_revenue"]}
    ev.put(f"ev:uc:{C}.customer_ltv:metadata", {"exists": True, "columns": [{"name": "cust_id", "type": "bigint"}]})
    qs = ontology.plan(impact, ev.facts, budget=4)
    intents = [q.intent for q in qs]
    assert intents[0] == "identifier_relationship"
    assert intents.count("identifier_relationship") == 1          # two customer joins -> one concept
    assert qs[0].concept == "customer" and len(qs[0].objects) == 3          # customers.cust_id shared by both joins
    assert "metric_definition" in intents


def test_questions_answered_by_unity_catalog_are_not_asked(scenario):
    impact, ev = scenario
    impact.head.entries["jobs.orders_etl.segment"]["outputs"] = {f"{C}.segment_revenue": ["net_revenue"]}
    ev.facts[f"ev:uc:{C}.revenue_metrics:metric_view"] = {"definition": "measures:\n  - name: net_revenue\n    expr: SUM(x)"}
    qs = ontology.plan(impact, ev.facts, budget=4)
    metric = next(q for q in qs if q.intent == "metric_definition")
    assert metric.status == "answered_by_uc" and metric.uc_answer.endswith(":metric_view")


def test_budget_is_enforced(scenario):
    impact, ev = scenario
    impact.head.entries["jobs.orders_etl.segment"]["outputs"] = {f"{C}.a": ["gross_revenue", "net_margin", "ltv"]}
    qs = ontology.plan(impact, ev.facts, budget=1)
    assert sum(q.status == "planned" for q in qs) == 1
    assert sum(q.status == "skipped_budget" for q in qs) >= 2


def test_citations_are_parsed_from_answer_links():
    text = ("See [revenue_metrics](https://ws/explore/data/main/shop/revenue_metrics?o=1) and "
            "[Query Result](https://ws/one/chat/threads/t?o=1#embed-query_ab12) and [Customer Identity](https://ws/pages/123)")
    cites = [ontology.classify_citation(l, u) for l, u in ontology.LINK.findall(text)]
    assert [c["kind"] for c in cites] == ["uc_object", "query", "uc_page"]
    assert cites[0]["ref"] == "main.shop.revenue_metrics"


def test_an_answer_is_authoritative_only_with_a_governed_citation():
    facts = {"ev:uc:main.shop.revenue_metrics:metric_view": {}, "ev:uc:main.shop.raw:tags": {"table_tags": {}}}
    gov = [{"kind": "uc_object", "ref": "main.shop.revenue_metrics"}]
    plain = [{"kind": "uc_object", "ref": "main.shop.raw"}]
    assert ontology.assess("ok", gov, facts) == (True, False)
    assert ontology.assess("ok", plain, facts) == (False, False)
    assert ontology.assess("ok", [], facts) == (False, False)        # a response alone is not validation
    assert ontology.assess("These two definitions conflict.", gov, facts)[1] is True


class _FakeGenieOne:
    def __init__(self, answer, status="completed"):
        self.answer, self.status = answer, status

    def ask(self, q):
        return {"status": self.status, "final_answer": self.answer, "query_items": [{"sql": "SELECT 1"}], "_seconds": 1.0}


def _question():
    return ontology.Question("q-1", "identifier_relationship", "customer", "?", [f"uc:{C}.orders.customer_id", f"uc:{C}.customers.cust_id"], ("a.sql", 9))


def test_unavailable_and_unanswered_are_explicit():
    q1, q2 = _question(), _question()
    assert ontology.ask([q1], None, {})[0].status == "unavailable"
    assert ontology.ask([q2], _FakeGenieOne("", status="failed"), {})[0].status == "no_answer"
    s = ontology.summary([q1], ontology.ask([_question()], None, {}), True)
    assert s["status"] == "Unavailable"


def _identifier_finding(ev_id):
    return Finding("ontology.identifier-relationship", "high", "a.sql", 9, "Joins CRM and canonical customer ids directly",
                   "e", [ev_id, f"ev:uc:{C}.customers:metadata"], source="genie")


def test_unsourced_ontology_answer_cannot_create_a_confident_finding():
    q = _question()
    facts = {f"ev:uc:{C}.customers:metadata": {"exists": True}}
    facts.update(ontology.to_evidence([q], ontology.ask([q], _FakeGenieOne("They are different systems."), facts)))
    f = _identifier_finding("ev:genie_one:q-1")
    normalize([f], facts, Policy())
    assert f.confidence == "low" and f.merge_impact == "informational"


def test_authoritative_ontology_answer_supports_the_finding_and_groups_with_type_mismatch():
    q = _question()
    facts = {f"ev:uc:{C}.customers:metadata": {"exists": True}, f"ev:uc:{C}.orders:metadata": {"exists": True}}
    page = ("Per [Customer Identity](https://ws/pages/42), [customers](https://ws/explore/data/main/shop/customers) "
            "cust_id is a separate CRM identifier.")
    facts.update(ontology.to_evidence([q], ontology.ask([q], _FakeGenieOne(page), facts)))
    onto = _identifier_finding("ev:genie_one:q-1")
    tm = Finding("contract.type-mismatch", "medium", "a.sql", 9, "Joins types", "e",
                 [f"ev:uc:{C}.orders:metadata", f"ev:uc:{C}.customers:metadata"])
    normalize([onto, tm], facts, Policy())
    assert onto.confidence == "high" and "Genie One" in onto.detected_by
    groups = correlate([onto, tm], facts)
    assert len(groups) == 1 and groups[0].primary.check == "ontology.identifier-relationship"   # one issue, not two
    assert {f.check for f in groups[0].findings} == {"ontology.identifier-relationship", "contract.type-mismatch"}


def test_summary_counts_and_rapid_mode():
    q_uc = ontology.Question("q-2", "metric_definition", "net revenue", "?", [], status="answered_by_uc")
    q_rapid = _question()
    q_rapid.status = "skipped_rapid"
    s = ontology.summary([q_uc, q_rapid], [], True)
    assert s["status"] == "Skipped" and "1 answered by Unity Catalog" in s["detail"] and "not asked (rapid mode)" in s["detail"]
    assert ontology.summary([], [], True)["status"] == "Not needed"
    assert ontology.summary([q_uc], [], False)["status"] == "Skipped"


def test_pages_from_other_domains_are_not_authoritative():
    cites = [{"kind": "uc_page", "ref": "u", "label": "Line Item"}, {"kind": "uc_page", "ref": "u2", "label": "Order"}]
    assert ontology.assess("ok", cites, {}, "customer", ["uc:main.shop.orders.customer_id"])[0] is False
    page = [{"kind": "uc_page", "ref": "u3", "label": "Customer Identity"}]
    assert ontology.assess("ok", page, {}, "customer", ["uc:main.shop.orders.customer_id"])[0] is False   # not tied to these tables
    tied = page + [{"kind": "uc_object", "ref": "main.shop.customers"}]
    assert ontology.assess("ok", tied, {}, "customer", ["uc:main.shop.orders.customer_id"])[0] is True
    disclaimer = "No glossary page covers customer identity in this schema; none of them applies here."
    assert ontology.assess(disclaimer, page, {}, "customer")[0] is False


def test_governed_objects_must_be_in_the_same_schema():
    facts = {"ev:uc:other.sales.metrics:metric_view": {}}
    cite = [{"kind": "uc_object", "ref": "other.sales.metrics"}]
    assert ontology.assess("ok", cite, facts, "net revenue", ["uc:main.shop.t.net_revenue"])[0] is False


def test_identifier_questions_skip_missing_tables_and_identical_columns(scenario):
    impact, ev = scenario
    impact.refs["jobs.orders_etl.segment"].joins = [
        {"left": [f"{C}.orders", "order_id"], "right": [f"{C}.order_items", "order_id"], "line": 11}]   # order_items missing
    assert not [q for q in ontology.plan(impact, ev.facts) if q.intent == "identifier_relationship"]

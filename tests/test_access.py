"""When access is missing, the review still returns something useful and says how to fix it."""

from types import SimpleNamespace

from databricks.sdk.errors import NotFound, PermissionDenied

from genie_review import config, pipeline
from genie_review.access import AccessReport, classify
from genie_review.evidence import collect
from genie_review.publish import MARKER, render_problem

SP = "00000000-0000-4000-8000-000000000001"


def test_access_report_writes_grants_for_the_actual_principal():
    r = AccessReport(SP)
    r.table("main.sales.orders")
    r.job("42")
    md = r.render()
    assert f"GRANT USE SCHEMA ON SCHEMA main.sales TO \\`{SP}\\`" in md
    assert '"permission_level":"CAN_VIEW"' in md and "jobs 42" in md


def test_classify():
    assert classify(PermissionDenied("User does not have USE SCHEMA")) == "permission denied"
    assert classify(NotFound("Table does not exist")) == "not found"
    assert classify("invalid_client: bad secret") == "authentication failed"


class _FakeTables:
    def get(self, fqn):
        if fqn.endswith(".secret_table"):
            raise PermissionDenied("User does not have USE SCHEMA on schema")
        raise NotFound("does not exist")


def _fake_client():
    return SimpleNamespace(tables=_FakeTables(), api_client=SimpleNamespace(do=lambda *a, **k: (_ for _ in ()).throw(PermissionDenied("no"))),
                           jobs=SimpleNamespace(get=lambda i: (_ for _ in ()).throw(PermissionDenied("no"))),
                           clusters=SimpleNamespace(spark_versions=lambda: SimpleNamespace(versions=[])))


def test_permission_errors_become_gaps_not_crashes(scenario):
    impact, _ = scenario
    impact.graph.node("uc:main.shop.secret_table", "table")
    impact.graph.edge("res:jobs.orders_etl.segment", "uc:main.shop.secret_table", "reads", "sql:sqlglot")
    impact.base_bundle = {"resources": {"jobs": {"orders_etl": {"tasks": []}}}}
    ev = collect(_fake_client(), impact, warehouse=None, principal=SP)
    areas = " ".join(ev.gaps)
    assert "secret_table" in areas                  # table metadata denied
    assert "SQL warehouse" in areas                 # no warehouse -> tags unavailable
    assert "Deployed job `42`" in areas             # job view denied
    assert ev.facts["ev:uc:main.shop.secret_table:metadata"]["access"] == "denied"


def test_sign_in_failure_posts_a_fix_it_comment(repo_with_pr, tmp_path):
    s = config.load({"repo": str(repo_with_pr), "base_ref": "main", "profile": "p", "out_dir": str(tmp_path / "out")})

    def broken(_profile):
        raise ValueError("default auth: cannot configure default credentials")

    result = pipeline.run(s, client_factory=broken, publish_to_github=False)
    assert "Review could not run" in result.body and "Could not sign in to Databricks" in result.body
    assert "LAKEHOUSE_REVIEW_CLIENT_ID" in result.body
    assert result.failed is False                   # setup problems do not block by default


def test_render_problem_is_a_review_comment():
    body = render_problem("Could not resolve the bundle", "validate failed", ["Check bundle-dir"])
    assert body.startswith(MARKER) and "1. Check bundle-dir" in body

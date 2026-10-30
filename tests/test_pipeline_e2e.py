"""The whole pipeline on a real git repository, with the workspace, bundle CLI, and Genie Code faked."""

from types import SimpleNamespace as NS

from genie_review import bundle, config, evidence, genie, pipeline, publish

from .conftest import git

BUNDLE_ROOT = "/Workspace/Shared/.bundle/shop/staging/files"
BASE_SQL = "CREATE OR REPLACE TABLE main.sales.daily AS\nSELECT o.order_id, o.amount\nFROM main.sales.orders o\n"
HEAD_SQL = "CREATE OR REPLACE TABLE main.sales.daily AS\nSELECT o.order_id, o.amount, o.discount\nFROM main.sales.orders o\n"


def _repo(tmp_path):
    r = tmp_path / "repo"
    (r / "src").mkdir(parents=True)
    (r / "databricks.yml").write_text("bundle: {name: shop}\n")
    (r / "src/daily.sql").write_text(BASE_SQL)
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    git(r, "checkout", "-qb", "pr")
    (r / "src/daily.sql").write_text(HEAD_SQL)
    git(r, "-c", "core.hooksPath=/dev/null", "commit", "-qam", "add discount")
    return r


def _fake_world(monkeypatch):
    resolved = {"bundle": {"target": "staging"}, "workspace": {"file_path": BUNDLE_ROOT},
                "resources": {"jobs": {"daily": {"tasks": [{"task_key": "build", "sql_task": {"file": {"path": f"{BUNDLE_ROOT}/src/daily.sql"}}}]}}}}
    monkeypatch.setattr(bundle, "resolve", lambda *a, **k: resolved)
    monkeypatch.setattr(bundle, "deployed_ids", lambda *a, **k: ({}, "not_deployed", ""))
    monkeypatch.setattr(evidence, "pick_warehouse", lambda w, wh: None)

    def collect(w, imp, warehouse, principal):
        ev = evidence.Evidence(principal)
        ev.put("ev:uc:main.sales.orders:metadata", {"exists": True, "columns": [{"name": "order_id", "type": "bigint"}, {"name": "amount", "type": "decimal(18,2)"}]})
        return ev
    monkeypatch.setattr(evidence, "collect", collect)
    monkeypatch.setattr(genie, "resolve_route", lambda s: (None, ["target workspace: model `m` unavailable (404)"]))
    posted = []
    monkeypatch.setattr(publish, "post_comment", lambda body: posted.append(body) or "https://github.com/acme/shop/pull/1#c1")
    return posted


def test_review_runs_end_to_end_and_sets_outputs(monkeypatch, tmp_path):
    posted = _fake_world(monkeypatch)
    out_file, summary = tmp_path / "gh_output", tmp_path / "gh_summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    r = _repo(tmp_path)
    s = config.load({"repo": str(r), "base_ref": "main", "profile": "p", "out_dir": str(tmp_path / "out")})
    me = NS(current_user=NS(me=lambda: NS(user_name="reviewer")), config=NS(host="https://ws"))
    result = pipeline.run(s, client_factory=lambda p: me)

    checks = {f.check for f in result.findings}
    assert "contract.column-mismatch" in checks, checks
    mismatch = next(f for f in result.findings if f.check == "contract.column-mismatch")
    assert mismatch.file == "src/daily.sql" and "discount" in mismatch.title + mismatch.explanation
    assert len(posted) == 1 and posted[0].startswith("<!-- genie-data-review -->\n# Lakehouse Review")
    assert "AI review engine: unavailable" in posted[0]                  # exact checks still posted
    outputs = dict(line.split("=", 1) for line in out_file.read_text().splitlines())
    assert outputs["issues"] != "0" and outputs["status"] in ("blocker", "action_required", "review_recommended")
    assert outputs["review_json"].endswith("review.json") and (tmp_path / "out/review.json").exists()
    assert outputs["comment_url"].endswith("#c1")
    assert "# Lakehouse Review" in summary.read_text() and "genie-data-review" not in summary.read_text()
    assert not result.failed                                              # fail-on defaults to none


def test_fail_on_gates_the_check(monkeypatch, tmp_path):
    _fake_world(monkeypatch)
    r = _repo(tmp_path)
    s = config.load({"repo": str(r), "base_ref": "main", "profile": "p", "out_dir": str(tmp_path / "out"), "fail_on": "high"})
    me = NS(current_user=NS(me=lambda: NS(user_name="reviewer")), config=NS(host="https://ws"))
    assert pipeline.run(s, client_factory=lambda p: me, publish_to_github=False).failed

"""`lakehouse-review setup` and `doctor` with a fake workspace."""

from types import SimpleNamespace as NS

from genie_review import doctor, reviewer_setup
from genie_review.config import Settings

SP = "00000000-1111-2222-3333-444444444444"


class FakeWorkspace:
    def __init__(self, readable=("main.sales",), fail_me=False):
        self.config = NS(host="https://ws.example.com")
        self.calls = []
        self._readable, self._fail_me = set(readable), fail_me
        self.current_user = NS(me=self._me)
        self.schemas = NS(get=self._schema)
        self.workspace = NS(get_status=lambda p: NS(object_id=99))
        self.statement_execution = NS(execute_statement=lambda **k: self.calls.append(("sql", k["statement"])) or NS(status=None))
        self.api_client = NS(do=lambda m, path, body=None: self.calls.append((m, path)))
        self.warehouses = NS(list=lambda: [NS(id="wh1", enable_serverless_compute=True, state="State.RUNNING")])

    def _me(self):
        if self._fail_me:
            raise RuntimeError("invalid client secret")
        return NS(user_name=SP, display_name="reviewer")

    def _schema(self, name):
        if name not in self._readable:
            raise RuntimeError("PERMISSION_DENIED")


def test_setup_prints_least_privilege_grants_and_changes_nothing():
    w = FakeWorkspace()
    plan = reviewer_setup.build(w, SP, {"main.sales", "main.finance", "samples.tpch"}, "wh1",
                                {"workspace": {"root_path": "/Workspace/Shared/.bundle/app/prod"}}, {"jobs": {"etl": "42"}})
    text = reviewer_setup.render(plan)
    assert f"GRANT USE CATALOG, BROWSE ON CATALOG `main` TO `{SP}`;" in text
    assert "USE SCHEMA" not in text and "SELECT" not in text and "MODIFY" not in text
    assert "`samples`" not in text.split("Notes:")[0] and "built-in catalog" in text
    assert "permissions update warehouses wh1" in text and "permissions update jobs 42" in text
    assert "permissions update directories 99" in text
    assert "Nothing was changed" in text and w.calls == []


def test_setup_run_applies_and_reports_each_step():
    w = FakeWorkspace()
    plan = reviewer_setup.build(w, SP, {"main.sales"}, "wh1", None, {"jobs": {"etl": "42"}})
    results = reviewer_setup.apply(plan)
    assert all(r == "ok" for r in results.values())
    assert ("sql", f"GRANT USE CATALOG, BROWSE ON CATALOG `main` TO `{SP}`") in w.calls
    assert ("PATCH", "/api/2.0/permissions/jobs/42") in w.calls
    assert ("PATCH", "/api/2.0/permissions/warehouses/wh1") in w.calls
    assert "OK   Use" not in reviewer_setup.render(plan, results)          # manual steps are never "applied"


def test_setup_flags_personal_deploy_folder():
    plan = reviewer_setup.build(None, SP, {"main.sales"}, None,
                                {"workspace": {"root_path": "/Workspace/Users/a@b.com/.bundle/app/dev"}}, {})
    assert any("personal folder" in n for n in plan.notes)
    assert any("warehouse" in n for n in plan.notes)


def _settings(tmp_path, **kw):
    return Settings(repo=tmp_path, base_ref="main", profile="p", out_dir=tmp_path / "out", **kw)


def test_doctor_reports_each_check_independently(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "schemas_in_bundle", lambda *a: ({"main.sales", "main.secret"}, {"bundle": {"target": "prod"}}))
    monkeypatch.setattr(doctor.bdl, "deployed_ids", lambda *a: ({}, "unreadable", "PERMISSION_DENIED on root_path"))
    monkeypatch.setattr(doctor.genie, "resolve_route", lambda s: (None, ["target workspace: model `m` unavailable (404)"]))
    monkeypatch.setattr(doctor.genie, "_token", lambda p: "t")
    monkeypatch.setattr(doctor.GenieOne, "tools", lambda self: ["genie_ask", "genie_poll_response", "genie_get_query_result"])
    res = {r.name: r for r in doctor.run(_settings(tmp_path), client=lambda p: FakeWorkspace())}
    assert res["Sign-in"].status == "OK" and res["Bundle"].status == "OK"
    assert res["Deployment state"].status == "WARN" and "CAN_READ" in res["Deployment state"].fix
    assert res["Unity Catalog"].status == "WARN" and "main.secret" in res["Unity Catalog"].detail
    assert res["Genie Code"].status == "WARN"            # exact checks still run, so not a failure by default
    assert res["Genie One"].status == "OK"
    assert doctor.exit_code(list(res.values())) == 0
    assert "Fix:" in doctor.render(list(res.values()))


def test_doctor_fails_fast_on_sign_in(tmp_path):
    res = doctor.run(_settings(tmp_path), client=lambda p: FakeWorkspace(fail_me=True))
    assert [r.name for r in res] == ["Configuration", "Sign-in"] and res[-1].status == "FAIL"
    assert doctor.exit_code(res) == 1


def test_doctor_model_failure_is_fatal_only_when_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "schemas_in_bundle", lambda *a: (set(), {}))
    monkeypatch.setattr(doctor.bdl, "deployed_ids", lambda *a: ({"jobs": {"x": "1"}}, "deployed", ""))
    monkeypatch.setattr(doctor.genie, "resolve_route", lambda s: (None, ["unavailable"]))
    res = doctor.run(_settings(tmp_path, on_model_unavailable="fail", semantic="off"), client=lambda p: FakeWorkspace())
    assert {r.name: r.status for r in res}["Genie Code"] == "FAIL" and doctor.exit_code(res) == 1

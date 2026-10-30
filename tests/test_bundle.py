from pathlib import Path

from genie_review.bundle import diff_resources, entries

ROOT = "/Workspace/Users/me/.bundle/demo/dev/files"


def _bundle(tasks, job_clusters=None):
    return {"workspace": {"file_path": ROOT},
            "resources": {"jobs": {"etl": {"name": "etl", "tasks": tasks, "job_clusters": job_clusters or []}}}}


def test_entries_map_tasks_to_repo_files(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/nb.py").write_text("")
    b = _bundle([
        {"task_key": "py", "spark_python_task": {"python_file": f"{ROOT}/src/job.py", "parameters": ["--catalog", "main"]}},
        {"task_key": "sql", "sql_task": {"file": {"path": f"{ROOT}/src/q.sql"}, "parameters": {"catalog": "main"}}},
        {"task_key": "nb", "notebook_task": {"notebook_path": f"{ROOT}/src/nb"}},
    ])
    got = {e.resource: (e.file, e.params) for e in entries(b, tmp_path)}
    assert got["jobs.etl.py"] == ("src/job.py", {"catalog": "main"})
    assert got["jobs.etl.sql"] == ("src/q.sql", {"catalog": "main"})
    assert got["jobs.etl.nb"][0] == "src/nb.py"


def test_diff_detects_added_tasks_and_compute_changes():
    t = {"task_key": "silver", "spark_python_task": {"python_file": "x.py"}, "job_cluster_key": "c"}
    base = _bundle([t], [{"job_cluster_key": "c", "new_cluster": {"spark_version": "15.4.x-scala2.12"}}])
    head = _bundle([t, {"task_key": "gold", "sql_task": {"file": {"path": "q.sql"}}}],
                   [{"job_cluster_key": "c", "new_cluster": {"spark_version": "16.4.x-scala2.13"}}])
    d = diff_resources(base, head)["jobs.etl"]
    assert d["change"] == "modified"
    assert d["tasks"]["gold"] == {"change": "added"}
    assert d["tasks"]["silver"]["kinds"] == ["compute"]


def test_diff_ignores_unchanged_resources():
    b = _bundle([{"task_key": "a"}])
    assert diff_resources(b, b) == {}


def test_deployment_state_separates_never_deployed_from_access_denied(monkeypatch, tmp_path):
    from genie_review import bundle
    from genie_review.util import CommandError

    monkeypatch.setattr(bundle, "sh", lambda *a, **k: '{"resources": {"jobs": {"etl": {"name": "etl"}}}}')
    assert bundle.deployed_ids(tmp_path, "p", None) == ({"jobs": {}}, "not_deployed", "")
    monkeypatch.setattr(bundle, "sh", lambda *a, **k: '{"resources": {"jobs": {"etl": {"id": 42}}}}')
    assert bundle.deployed_ids(tmp_path, "p", None)[:2] == ({"jobs": {"etl": "42"}}, "deployed")

    def denied(*a, **k):
        raise CommandError("databricks bundle summary ... failed (1): Error: PERMISSION_DENIED: no access to /Workspace/x")
    monkeypatch.setattr(bundle, "sh", denied)
    ids, state, detail = bundle.deployed_ids(tmp_path, "p", None)
    assert (ids, state) == ({}, "unreadable") and "PERMISSION_DENIED" in detail

    def missing_var(*a, **k):
        raise CommandError("databricks bundle summary ... failed (1): Error: no value assigned to required variable catalog")
    monkeypatch.setattr(bundle, "sh", missing_var)
    assert bundle.deployed_ids(tmp_path, "p", None)[1] == "unknown"

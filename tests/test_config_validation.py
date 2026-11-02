"""Bad inputs and config files produce one clear list of problems, not a stack trace."""

import pytest

from genie_review import cli, config

from .conftest import git


def _repo(tmp_path, cfg: str | None):
    r = tmp_path / "r"
    (r / ".lakehouse-review").mkdir(parents=True)
    (r / "databricks.yml").write_text("bundle: {name: x}\n")
    if cfg is not None:
        (r / ".lakehouse-review/config.yml").write_text(cfg)
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    return r


def _load(r, **kw):
    return config.load({"repo": str(r), "base_ref": "main", "profile": "p", **kw})


def test_all_problems_are_reported_together(tmp_path):
    r = _repo(tmp_path, "fail_on: low\nmax_findings: lots\n"
                        "checks: {severity: {deploy.drift: huge}}\nontology: {max_questions: 99}\n")
    with pytest.raises(config.ConfigError) as e:
        _load(r, depth="deep")
    text = "\n".join(e.value.problems)
    for needle in ("fail-on", "analysis-depth", "max_findings", "checks.severity.deploy.drift", "ontology.max_questions"):
        assert needle in text, needle
    assert e.value.source == ".lakehouse-review/config.yml"


def test_invalid_yaml_and_wrong_shape(tmp_path):
    with pytest.raises(config.ConfigError, match="not valid YAML"):
        _load(_repo(tmp_path / "a", "fail_on: [unclosed\n"))
    with pytest.raises(config.ConfigError, match="must be a mapping"):
        _load(_repo(tmp_path / "b", "- just a list\n"))


def test_bundle_dir_must_stay_inside_the_repo(tmp_path):
    with pytest.raises(config.ConfigError, match="bundle-dir"):
        _load(_repo(tmp_path, None), bundle_dir="../elsewhere")


def test_missing_explicit_config_file_is_an_error(tmp_path):
    with pytest.raises(config.ConfigError, match="not found on the base branch"):
        _load(_repo(tmp_path, None), config_file="ci/review.yml")


def test_valid_config_loads(tmp_path):
    s = _load(_repo(tmp_path, "fail_on: blocker\nmax_findings: 5\nontology: {max_questions: 2}\ngenie: {model: m}\n"))
    assert (s.fail_on, s.max_findings, s.ontology_budget, s.genie_model) == ("blocker", 5, 2, "m")


def test_unknown_or_removed_settings_fail_clearly(tmp_path):
    with pytest.raises(config.ConfigError, match="unknown settings: merge_policy, report"):
        _load(_repo(tmp_path, "report: {max_priority: 2}\nmerge_policy: {block_severities: [high]}\n"))


def test_cli_posts_problem_and_fails(tmp_path, capsys):
    r = _repo(tmp_path, "fail_on: low\n")
    code = cli.main(["run", "--repo", str(r), "--base-ref", "main", "--profile", "p", "--no-publish", "--out-dir", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert code == 1
    assert "::error title=Lakehouse Review::`fail-on` must be one of" in out
    assert "The configuration is not valid" in out and "must be merged first" in out
    assert (tmp_path / "out/review.md").exists()

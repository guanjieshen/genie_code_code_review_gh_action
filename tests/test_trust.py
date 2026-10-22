"""The pull request must not be able to change how it is reviewed."""

from genie_review import config
from genie_review.genie import Route, sanitized_worktree, write_home

from .conftest import git


def test_config_and_instructions_come_from_base_branch(repo_with_pr):
    s = config.load({"repo": str(repo_with_pr), "base_ref": "main", "profile": "p"})
    assert s.fail_on == "critical"          # PR tried to set none
    assert s.disable == ["deploy.drift"]
    assert s.config_source == ".genie-review/config.yml"


def test_inputs_override_config_file(repo_with_pr):
    s = config.load({"repo": str(repo_with_pr), "base_ref": "main", "profile": "p", "fail_on": "high"})
    assert s.fail_on == "high"


def test_sanitized_worktree_restores_agent_config_from_base(repo_with_pr, tmp_path):
    tree = sanitized_worktree(repo_with_pr, "main", tmp_path / "tree")
    try:
        assert (tree / "AGENTS.md").read_text() == "Base instructions.\n"
        assert not (tree / ".agents").exists()
        assert (tree / ".genie-review/config.yml").read_text().startswith("fail_on: critical")
        assert (tree / "a.py").exists()
    finally:
        git(repo_with_pr, "worktree", "remove", "--force", str(tree))


def test_write_home_allowlists_genie_one_tools(tmp_path):
    write_home(tmp_path, Route("p", "https://ws", "123", "gpt-5.6-sol", "target workspace"), "https://ws")
    cfg = (tmp_path / "config.toml").read_text()
    assert 'base_url = "https://ws/ai-gateway/codex/v1"' in cfg
    assert 'enabled_tools = ["genie_ask", "genie_poll_response", "genie_get_query_result"]' in cfg
    assert "genie_cancel_response" not in cfg
    assert 'default_tools_approval_mode = "approve"' in cfg
    assert '"-m", "genie_review.token", "--profile", "p"' in cfg   # works for service principals, not only user logins


def test_monorepo_config_is_read_from_bundle_dir(tmp_path):
    r = tmp_path / "mono"
    (r / "team/app/.genie-review").mkdir(parents=True)
    (r / "team/app/.genie-review/config.yml").write_text("fail_on: high\ninstructions_file: .genie-review/notes.md\n")
    (r / "team/app/.genie-review/notes.md").write_text("Prod is main.\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    s = config.load({"repo": str(r), "base_ref": "main", "profile": "p", "bundle_dir": "team/app"})
    assert s.fail_on == "high" and s.instructions == "Prod is main." and s.bundle_dir == "team/app"

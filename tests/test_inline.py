"""Comments on code lines: which lines can take one, what it says, and posting each issue once."""

import json

from genie_review import pipeline, publish, render
from genie_review.config import Settings

from .conftest import git
from .sample_review import build


def test_commentable_lines_are_the_hunks_of_the_head_version(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    (r / "a.sql").write_text("".join(f"line {i}\n" for i in range(1, 31)))
    git(r, "init", "-q", "-b", "main")
    git(r, "add", "-A")
    git(r, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    git(r, "checkout", "-qb", "pr")
    (r / "a.sql").write_text("".join(f"line {i}\n" if i != 20 else "changed\n" for i in range(1, 31)))
    (r / "new.sql").write_text("select 1\nselect 2\n")
    git(r, "add", "-A")
    git(r, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "pr")
    lines = pipeline.commentable_lines(Settings(repo=r, base_ref="main", profile="p", out_dir=tmp_path / "o"))
    assert lines["a.sql"] == set(range(17, 24))          # the change plus 3 lines of context each side
    assert lines["new.sql"] == {1, 2}


def test_inline_body_is_short_and_marked():
    review, _ = build()
    g = review.groups[0]
    body = render.inline_body(g)
    assert body.splitlines()[0] == render.inline_marker(g.primary.check)
    assert g.primary.title in body and "Recommended fix" in body
    assert "Technical evidence" not in body                # evidence stays in the review comment


class FakeGitHub:
    def __init__(self, existing_bodies=(), review_ok=True):
        self.existing, self.review_ok, self.posted = list(existing_bodies), review_ok, []

    def get(self, url, headers, params, timeout):
        return Resp(self.existing if params["page"] == 1 else [])

    def post(self, url, headers, json, timeout):
        if url.endswith("/reviews"):
            if not self.review_ok:
                return Resp({}, ok=False)
            self.posted += json["comments"]
            return Resp({})
        ok = json["line"] != 99                              # GitHub refuses a line outside the diff
        if ok:
            self.posted.append(json)
        return Resp({}, ok=ok)


class Resp:
    def __init__(self, data, ok=True):
        self.data, self.ok = data, ok

    def json(self):
        return self.data

    def raise_for_status(self):
        pass


def _env(monkeypatch, tmp_path, gh):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 7, "head": {"sha": "abc"}}}))
    for k, v in {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "acme/shop", "GITHUB_EVENT_PATH": str(event)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(publish, "requests", gh)


def _comment(check, line=3, path="src/a.sql"):
    return {"check": check, "path": path, "line": line, "body": f"<!-- lakehouse-review:{check} -->\nissue {check} at {line}"}


def test_each_issue_is_commented_once_across_pushes(monkeypatch, tmp_path):
    earlier = [{"path": "src/a.sql", "line": 12, "body": "<!-- lakehouse-review:semantic.filter-meaning -->\nold wording"}]
    gh = FakeGitHub(existing_bodies=earlier)
    _env(monkeypatch, tmp_path, gh)
    new = [_comment("semantic.filter-meaning", 13),                  # same issue, moved one line: skipped
           _comment("semantic.filter-meaning", 30),                  # same check far away: a different issue
           _comment("semantic.filter-meaning", 13, "src/b.sql"),     # another file
           _comment("contract.column-mismatch", 12)]                 # another check on the same line
    assert publish.post_inline(new) == 3
    assert gh.posted[0]["side"] == "RIGHT"


def test_a_refused_line_does_not_drop_the_other_comments(monkeypatch, tmp_path):
    gh = FakeGitHub(review_ok=False)
    _env(monkeypatch, tmp_path, gh)
    assert publish.post_inline([_comment("g-1"), _comment("g-2", line=99)]) == 1
    assert gh.posted[0]["commit_id"] == "abc"


def test_nothing_is_posted_outside_a_pull_request(monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert publish.post_inline([_comment("g-1")]) == 0

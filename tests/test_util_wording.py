"""Small helpers: shell, logging, JSON output, and finding wording."""

import json

import pytest

from genie_review import util, wording


def test_sh_returns_stdout_and_raises_with_stderr(tmp_path):
    assert util.sh(["printf", "ok"]) == "ok"
    with pytest.raises(util.CommandError, match="boom"):
        util.sh(["bash", "-c", "echo boom >&2; exit 3"])
    assert util.sh(["bash", "-c", "exit 3"], check=False) == ""


def test_log_and_group_go_to_stderr(capsys):
    util.log("hello")
    with util.group("Stage"):
        pass
    err = capsys.readouterr().err
    assert "[lakehouse-review] hello" in err and "::group::Stage" in err and "::endgroup::" in err


def test_write_json_creates_folders(tmp_path):
    util.write_json(tmp_path / "a/b.json", {"x": [1, 2]})
    assert json.loads((tmp_path / "a/b.json").read_text()) == {"x": [1, 2]}
    assert util.is_empty({}) and util.is_empty("") and not util.is_empty(0)


def test_short_names_and_overlap():
    assert wording.short("main.sales.orders") == "orders"
    assert wording.overlaps("Join uses the wrong customer key", "join uses wrong customer key")
    assert not wording.overlaps("Join uses the wrong customer key", "Runtime is not supported")


def test_exact_wording_is_specific_for_every_rule_it_knows():
    params = {"table": "main.sales.orders", "column": "discount", "live": ["amount", "order_id"]}
    out = wording.exact("contract.column-mismatch", params)
    assert "discount" in out.get("title", "") + out.get("explanation", "")
    assert wording.exact("no.such-rule", {}) == {}          # unknown rules keep the check's own wording

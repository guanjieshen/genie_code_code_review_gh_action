"""The PR comment always fits GitHub's size limit; the job summary keeps the full report."""

from genie_review.render import COMMENT_LIMIT, SHORTENED, LinkContext, fit_comment, markdown, outputs, status_key

from .sample_review import build

CTX = LinkContext("https://github.com", "acme/data-platform", "0123abc", ".")


def test_small_report_is_unchanged():
    review, md = build()
    assert fit_comment(review, CTX) == md
    assert len(md) < COMMENT_LIMIT


def test_compact_report_keeps_needs_attention_and_lists_the_rest():
    review, md = build()
    full = len(md)
    compact = fit_comment(review, CTX, limit=full - 1)
    assert SHORTENED in compact and len(compact) < full
    assert "## Needs attention" in compact
    for g in review.groups:                       # every issue is still listed
        assert g.primary.title in compact
    assert compact.count("<details>") == compact.count("</details>")


def test_hard_cut_closes_open_sections():
    review, _ = build()
    compact = markdown(review, CTX, compact=True)
    cut = fit_comment(review, CTX, limit=len(compact) // 2)
    assert len(cut) <= len(compact) // 2 and cut.rstrip().endswith(SHORTENED)
    assert cut.count("<details>") <= cut.count("</details>")


def test_outputs_are_machine_readable():
    review, _ = build()
    out = outputs(review)
    assert out["issues"] == len(review.groups) == 10
    assert out["status"] == status_key(review) in ("blocker", "action_required")
    assert out["blockers"] == sum(g.merge_impact == "blocker" for g in review.groups)
    empty, _ = build(findings=[])
    assert outputs(empty) == {"status": "no_issues", "issues": 0, "blockers": 0}

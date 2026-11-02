"""Validate, ground, merge, render, and publish findings. The only module that talks to GitHub."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import jsonschema
import requests

from .checks import CATALOG
from .genie import GenieRun
from .impact import ImpactResult
from .models import SEVERITIES, Finding, PlanItem
from .prompt import resource_text

from .render import MARKER, render_problem  # noqa: E402,F401  (re-exported for callers)


# ------------------------------------------------------------------ sanitize model text


def clean(text: str, limit: int = 1200) -> str:
    """Strip links, images, HTML, and @-mentions from model text before it reaches a PR comment."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)            # images (exfiltration channel)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)         # links -> label
    text = re.sub(r"<[^>]+>", "", text)                          # html
    text = re.sub(r"https?://\S+", "[link removed]", text)
    text = re.sub(r"(?<![\w`])@([\w-]+)", r"@&#8203;\1", text)   # neutralize mentions
    return text.strip()[:limit]


# ------------------------------------------------------------------ validate + ground


def genie_findings(run: GenieRun, plan: list[PlanItem], evidence_ids: set[str]) -> tuple[list[Finding], list[str], str]:
    if not run.output:
        return [], [], ""
    run.output.setdefault("ontology_verdicts", [])   # older outputs had no verdicts
    notes: list[str] = []
    try:
        jsonschema.validate(run.output, json.loads(resource_text("findings.schema.json")))
    except jsonschema.ValidationError as e:
        return [], [f"Genie output failed schema validation: {e.message[:120]}"], ""
    planned = {p.check for p in plan}
    out = []
    for f in run.output["findings"]:
        if f["check"] not in planned:
            notes.append(f"dropped `{f['check']}` finding (check not planned)")
            continue
        grounded = [e for e in f["evidence"] if e in evidence_ids]
        if not grounded:
            notes.append(f"dropped ungrounded finding: {f['title'][:60]}")
            continue
        out.append(Finding(f["check"], f["severity"], f["file"], f["line"], clean(f["title"], 160), clean(f["explanation"]),
                           grounded, clean(f["suggestion"], 400), f["confidence"], "genie", impact=clean(f.get("impact", ""), 400)))
    return out, notes, clean(run.output.get("impact_summary", ""), 1500)


def merge(det: list[Finding], gen: list[Finding], severity_overrides: dict[str, str], cap: int | None = None) -> list[Finding]:
    """Combine exact and Genie findings. A Genie finding that repeats an exact one (same check and place) is dropped.
    Nothing else is discarded here; display limits are applied when rendering."""
    merged = list(det)
    for g in gen:
        dup = any(d.check == g.check and d.file == g.file and (abs(d.line - g.line) <= 2 or set(d.evidence) & set(g.evidence))
                  for d in merged)
        if not dup:
            merged.append(g)
    for f in merged:
        if f.check in severity_overrides:
            f.severity = severity_overrides[f.check]
    merged.sort(key=lambda f: (SEVERITIES.index(f.severity), f.file, f.line))
    return merged[:cap] if cap else merged


# ------------------------------------------------------------------ render


def fallback_summary(impact: ImpactResult) -> str:
    g = impact.graph
    res = [n["id"][4:] for n in g.nodes.values() if n["kind"] in ("task", "pipeline")]
    changed = [f"`{n['id'][3:]}` ({n['change'].replace('_', ' ')})" for n in g.nodes.values()
               if n["kind"] == "table" and n.get("change")]
    lines = []
    if res:
        lines.append(f"Resources: {', '.join(f'`{r}`' for r in res)}.")
    if changed:
        lines.append(f"Tables written: {', '.join(changed)}.")
    reads = [t for t in g.tables_with("reads") if t not in g.tables_with("writes")]
    if reads:
        lines.append(f"Tables read: {len(reads)}.")
    return "\n".join(lines) or "No Databricks resources are affected by this PR."


# ------------------------------------------------------------------ GitHub


def _pull_request() -> dict[str, Any] | None:
    """API base, repo, PR number, head commit, and auth headers for the current pull_request event, if any."""
    token, repo, event_path = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_EVENT_PATH")
    if not (token and repo and event_path):
        return None
    pr = json.load(open(event_path)).get("pull_request") or {}
    if not pr.get("number"):
        return None
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    return {"url": f"{api}/repos/{repo}", "number": pr["number"], "sha": (pr.get("head") or {}).get("sha", ""),
            "headers": {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}}


def _all(url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, 10):
        r = requests.get(url, headers=headers, params={"per_page": 100, "page": page}, timeout=30)
        r.raise_for_status()
        out += r.json()
        if len(r.json()) < 100:
            break
    return out


def post_comment(body: str) -> str | None:
    """Create or update the one review comment on the pull request."""
    pr = _pull_request()
    if not pr:
        return None
    h, comments = pr["headers"], f"{pr['url']}/issues/{pr['number']}/comments"
    existing = next((c for c in _all(comments, h) if MARKER in c.get("body", "")), None)
    if existing:
        r = requests.patch(existing["url"], headers=h, json={"body": body}, timeout=30)
    else:
        r = requests.post(comments, headers=h, json={"body": body}, timeout=30)
    r.raise_for_status()
    return r.json().get("html_url")


def post_inline(comments: list[dict[str, Any]]) -> int:
    """Comment on code lines: {path, line, body} each, where body carries a hidden finding marker.

    Comments already posted on an earlier push (same marker) are skipped, so each issue is commented once.
    Returns how many were posted.
    """
    pr = _pull_request()
    if not pr or not comments:
        return 0
    h = pr["headers"]
    posted = "\n".join(c.get("body", "") for c in _all(f"{pr['url']}/pulls/{pr['number']}/comments", h))
    new = [c for c in comments if c["body"].splitlines()[0] not in posted]
    if not new:
        return 0
    review = {"commit_id": pr["sha"], "event": "COMMENT",
              "comments": [{"path": c["path"], "line": c["line"], "side": "RIGHT", "body": c["body"]} for c in new]}
    r = requests.post(f"{pr['url']}/pulls/{pr['number']}/reviews", headers=h, json=review, timeout=30)
    if r.ok:
        return len(new)
    # One bad line rejects the whole review: post the comments one by one and skip the ones GitHub refuses.
    count = 0
    for c in review["comments"]:
        one = requests.post(f"{pr['url']}/pulls/{pr['number']}/comments", headers=h, json={"commit_id": pr["sha"], **c}, timeout=30)
        count += one.ok
    return count


def step_summary(body: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(body.replace(MARKER, "") + "\n")


def set_outputs(**kv: Any) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")


def should_fail(findings: list[Finding], fail_on: str) -> bool:
    if fail_on == "none":
        return False
    if fail_on == "blocker":
        return any(f.merge_impact == "blocker" for f in findings)
    limit = SEVERITIES.index(fail_on)
    return any(SEVERITIES.index(f.severity) <= limit for f in findings)


__all__ = ["CATALOG"]

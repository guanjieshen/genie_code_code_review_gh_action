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


def genie_findings(run: GenieRun, plan: list[PlanItem], evidence_ids: set[str], min_conf: float) -> tuple[list[Finding], list[str], str]:
    if not run.output:
        return [], [], ""
    notes: list[str] = []
    try:
        jsonschema.validate(run.output, json.loads(resource_text("findings.schema.json")))
    except jsonschema.ValidationError as e:
        return [], [f"Genie output failed schema validation: {e.message[:120]}"], ""
    planned = {p.check for p in plan}
    asked = [q.lower() for q in run.mcp_questions]
    out = []
    for f in run.output["findings"]:
        if f["check"] not in planned:
            notes.append(f"dropped `{f['check']}` finding (check not planned)")
            continue
        grounded = []
        for e in f["evidence"]:
            if e in evidence_ids:
                grounded.append(e)
            elif e.startswith("genie_one:") and any(e[10:].strip().lower()[:50] in q or q[:50] in e.lower() for q in asked):
                grounded.append(e)
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


def post_comment(body: str) -> str | None:
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not (token and repo and event_path):
        return None
    event = json.load(open(event_path))
    pr = (event.get("pull_request") or {}).get("number")
    if not pr:
        return None
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    existing = None
    page = 1
    while page < 10:
        r = requests.get(f"{api}/repos/{repo}/issues/{pr}/comments", headers=h, params={"per_page": 100, "page": page}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        existing = next((c for c in batch if MARKER in c.get("body", "")), existing)
        if len(batch) < 100:
            break
        page += 1
    if existing:
        r = requests.patch(existing["url"], headers=h, json={"body": body}, timeout=30)
    else:
        r = requests.post(f"{api}/repos/{repo}/issues/{pr}/comments", headers=h, json={"body": body}, timeout=30)
    r.raise_for_status()
    return r.json().get("html_url")


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

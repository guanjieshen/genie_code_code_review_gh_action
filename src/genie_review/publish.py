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

MARKER = "<!-- genie-data-review -->"
SEV_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}


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
        if f["confidence"] < min_conf:
            continue
        out.append(Finding(f["check"], f["severity"], f["file"], f["line"], clean(f["title"], 160), clean(f["explanation"]),
                           grounded, clean(f["suggestion"], 400), f["confidence"], "genie"))
    return out, notes, clean(run.output.get("impact_summary", ""), 1500)


def merge(det: list[Finding], gen: list[Finding], severity_overrides: dict[str, str], cap: int) -> list[Finding]:
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
    return merged[:cap]


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


def render_problem(title: str, detail: str, fixes: list[str]) -> str:
    out = [MARKER, "## Genie data review", "", f"⚠️ **The review could not run: {title}.**", "", clean(detail, 800), "",
           "**How to fix it**", ""]
    out += [f"{i}. {f}" for i, f in enumerate(fixes, 1)]
    out += ["", "<sub>This does not block the pull request. Set `fail-on-error: true` to make it fail the check.</sub>"]
    return "\n".join(out)


def render(impact: ImpactResult, findings: list[Finding], summary: str, coverage: list[dict[str, Any]],
           route_note: str, notes: list[str], access_md: str = "") -> str:
    out = [MARKER, "## Genie data review", ""]
    out += [summary or fallback_summary(impact), ""]
    if findings:
        counts = {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES}
        out.append("**" + " · ".join(f"{SEV_ICON[s]} {n} {s}" for s, n in counts.items() if n) + "**")
        out.append("")
        for f in findings:
            loc = f"`{f.file}:{f.line}`" if f.file else ""
            out.append(f"#### {SEV_ICON[f.severity]} {f.title}")
            out.append(f"{loc} · `{f.check}`" + (" · found by Genie" if f.source == "genie" else ""))
            out.append("")
            out.append(f.explanation)
            if f.suggestion:
                out.append(f"\n**Suggestion:** {f.suggestion}")
            out.append(f"\n<sub>Evidence: {', '.join(f'`{e[:90]}`' for e in f.evidence)}</sub>\n")
    else:
        out.append("No data issues found for the checks that apply to this PR.")
        out.append("")
    if access_md:
        out += ["### Access the reviewer is missing", "",
                "Some checks were limited because the reviewer identity could not read these. Findings above are still valid.", "",
                access_md, ""]
    out.append("<details><summary>Checks and coverage</summary>\n")
    out.append("| Check | Result |\n|---|---|")
    out += [f"| `{r['check']}` | {r['status']} |" for r in coverage]
    unresolved = impact.graph.unresolved
    if unresolved:
        out.append("\n**Could not resolve:** " + "; ".join(f"`{u['file']}:{u['line']}` {u['partial'][:60]}" for u in unresolved[:5]))
    if notes:
        out.append("\n**Notes:** " + "; ".join(notes[:6]))
    out.append(f"\n{route_note}\n</details>")
    return "\n".join(out)


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
    limit = SEVERITIES.index(fail_on)
    return any(SEVERITIES.index(f.severity) <= limit for f in findings)


__all__ = ["CATALOG"]

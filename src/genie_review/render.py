"""Rendering: one normalized review -> GitHub Markdown and structured JSON.

Pure functions. The same review always renders to the same Markdown (no timestamps, stable order).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .correlate import Group
from .models import CATEGORIES, SEVERITIES, Finding

MARKER = "<!-- genie-data-review -->"
ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "info": "⚪"}
STATUS = {
    "blocker": "Blocking issues found",
    "action_required": "Action required",
    "review_recommended": "Review recommended",
    "informational": "No action needed",
}
CONFIDENCE_LABEL = {"confirmed": "Confirmed", "high": "High confidence", "medium": "Needs validation", "low": "Low confidence"}


@dataclass
class LinkContext:
    """Where file links point. Empty server/repo/sha renders plain paths (local runs)."""

    server: str = ""
    repo: str = ""
    sha: str = ""
    bundle_dir: str = "."

    def display(self, path: str) -> str:
        prefix = "" if self.bundle_dir in ("", ".") else self.bundle_dir.rstrip("/") + "/"
        return path[len(prefix):] if prefix and path.startswith(prefix) else path

    def link(self, path: str, line: int) -> str:
        label = f"`{self.display(path)}:{line}`" if line else f"`{self.display(path)}`"
        if not (self.server and self.repo and self.sha and path):
            return label
        return f"[{label}]({self.server}/{self.repo}/blob/{self.sha}/{path}#L{line})"


@dataclass
class Review:
    """Everything the renderer needs. Built by the pipeline; also serialized as review.json."""

    summary: str
    groups: list[Group]                 # visible issues, already ordered
    priority: list[Group]               # subset shown under "Needs attention"
    findings: list[Finding]             # every finding, including hidden ones
    hidden: int = 0                     # findings suppressed from the comment (low confidence, filters)
    coverage: list[dict[str, Any]] = field(default_factory=list)
    access_md: str = ""
    access_count: int = 0
    route_note: str = ""
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ helpers


def cell(text: str) -> str:
    """Escape text for a Markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _counts(findings: list[Finding]) -> str:
    return " · ".join(f"{n} {s}" for s in SEVERITIES if (n := sum(f.severity == s for f in findings)))


def status(review: Review) -> str:
    if not review.groups:
        partial = review.access_count or any(r["status"] in ("could not run", "insufficient evidence") for r in review.coverage)
        return "No issues found in the checks that ran" if partial else "No issues found"
    top = min((g.merge_impact for g in review.groups), key=["blocker", "action_required", "review_recommended", "informational"].index)
    return STATUS[top]


def _short_objects(objs: list[str]) -> str:
    names = []
    for o in objs:
        kind, _, name = o.partition(":")
        names.append(f"`{name.split('.')[-1] if kind == 'uc' else o}`")
    return ", ".join(names)


# ------------------------------------------------------------------ finding blocks


def finding_block(g: Group, ctx: LinkContext) -> str:
    f = g.primary
    lines = [f"**{ICON[g.severity]} {_cap(g.severity)} — {f.title}**", "", f.explanation]
    if f.impact:
        lines += ["", f"**Impact:** {f.impact}"]
    if f.suggestion:
        lines += ["", f"**Recommended fix:** {f.suggestion}"]
    sources = list(dict.fromkeys(src for m in g.findings for src in m.detected_by))
    meta = [ctx.link(f.file, f.line), " + ".join(sources) or "Code analysis", CONFIDENCE_LABEL[g.confidence]]
    lines += ["", " · ".join(m for m in meta if m)]
    others = g.findings[1:]
    if others:
        lines += ["", "Also flagged: " + "; ".join(f"{o.title} (`{o.check}`)" for o in others)]
    lines += ["", evidence_block(g)]
    return "\n".join(lines)


def evidence_block(g: Group) -> str:
    rows = ["<details><summary>Technical evidence</summary>", ""]
    rows.append(f"- Checks: {', '.join(f'`{c}`' for c in g.checks)}")
    rows.append(f"- Merge impact: {g.merge_impact.replace('_', ' ')} ({g.reason})")
    objs = sorted({o for f in g.findings for o in f.affected_objects})
    if objs:
        rows.append(f"- Objects: {', '.join(f'`{o}`' for o in objs)}")
    ev = sorted({e for f in g.findings for e in f.evidence})
    rows.append("- Evidence: " + ", ".join(f"`{e[:120]}`" for e in ev))
    rows.append(f"- Finding ids: {', '.join(f'`{f.id}`' for f in g.findings)}")
    rows += ["", "</details>"]
    return "\n".join(rows)


def compact_ref(g: Group) -> str:
    return f"- {ICON[g.severity]} {g.primary.title} (shown in **Needs attention**)"


# ------------------------------------------------------------------ report


def markdown(review: Review, ctx: LinkContext, max_summary_lines: int = 5) -> str:
    out = [MARKER, "# Lakehouse Review", ""]
    summary = "\n".join([l for l in review.summary.strip().splitlines() if l.strip()][:max_summary_lines])
    out += [summary or "No Databricks resources are affected by this pull request.", ""]
    visible_findings = [f for g in review.groups for f in g.findings]
    out.append(f"**Review status:** {status(review)}  ")
    if visible_findings:
        line = f"**Findings:** {_counts(visible_findings)}"
        if len(review.groups) != len(visible_findings):
            line += f" ({len(review.groups)} issues from {len(visible_findings)} checks)"
        out.append(line)
    if review.access_count:
        out += ["", f"> ⚠️ **Partial review.** The reviewer could not read {review.access_count} thing(s), so some checks were limited. "
                    "See **Coverage** below for the grants to add."]
    out.append("")

    if review.groups:
        by_cat: dict[str, list[Group]] = {}
        for g in review.groups:
            by_cat.setdefault(g.category, []).append(g)
        cat_order = list(by_cat)  # groups are pre-sorted, so first appearance = highest priority
        out += ["| Review area | Findings | Priority |", "|---|---:|---|"]
        for c in cat_order:
            n = sum(len(g.findings) for g in by_cat[c])
            top = min((g.severity for g in by_cat[c]), key=SEVERITIES.index)
            out.append(f"| {CATEGORIES[c]} | {n} | {ICON[top]} {_cap(top)} |")
        out.append("")

        out += ["## Needs attention", ""]
        if review.priority:
            for i, g in enumerate(review.priority):
                if i:
                    out += ["---", ""]
                out += [finding_block(g, ctx), ""]
            rest = [g for g in review.groups if g not in review.priority and g.merge_impact in ("blocker", "action_required")]
            if rest:
                out += [f"{len(rest)} more issue(s) need action. See the review areas below.", ""]
        else:
            out += ["Nothing requires action before merging. Review the recommendations below.", ""]

        out += ["## Review details", ""]
        for c in cat_order:
            gs = by_cat[c]
            n = sum(len(g.findings) for g in gs)
            top = min((g.severity for g in gs), key=SEVERITIES.index)
            out += [f"<details><summary><b>{CATEGORIES[c]}</b> — {n} finding{'s' if n != 1 else ''} · {_cap(top)}</summary>", ""]
            for g in gs:
                out += [compact_ref(g) if g in review.priority else finding_block(g, ctx), ""]
            out += ["</details>", ""]

    out += coverage_section(review)
    if review.hidden:
        out += [f"<sub>{review.hidden} low-confidence or filtered observation(s) are not shown. They are in the review JSON.</sub>", ""]
    out.append(f"<sub>{review.route_note}</sub>")
    return "\n".join(out).rstrip() + "\n"


def coverage_section(review: Review) -> list[str]:
    if not (review.coverage or review.access_md or review.notes):
        return []
    rows = ["<details><summary>Coverage</summary>", ""]
    if review.coverage:
        rows += ["| Check | Result |", "|---|---|"]
        rows += [f"| `{r['check']}` | {cell(r['status'])} |" for r in review.coverage]
    if review.access_md:
        rows += ["", "**Access the reviewer is missing**", "", review.access_md]
    if review.notes:
        rows += ["", "**Notes:** " + cell("; ".join(review.notes[:6]))]
    rows += ["", "</details>", ""]
    return rows


def to_json(review: Review, version: str) -> dict[str, Any]:
    visible_ids = {f.id for g in review.groups for f in g.findings}
    return {
        "schema_version": 2,
        "tool_version": version,
        "status": status(review),
        "summary": review.summary,
        "counts": {s: sum(f.severity == s for g in review.groups for f in g.findings) for s in SEVERITIES},
        "groups": [g.to_dict() for g in review.groups],
        "priority": [g.id for g in review.priority],
        "findings": [{**f.to_dict(), "visible": f.id in visible_ids} for f in review.findings],
        "coverage": review.coverage,
        "route": review.route_note,
        "notes": review.notes,
    }


def render_problem(title: str, detail: str, fixes: list[str]) -> str:
    out = [MARKER, "# Lakehouse Review", "", "**Review status:** Review could not run", "",
           f"⚠️ **{title}.**", "", detail, "", "**How to fix it**", ""]
    out += [f"{i}. {f}" for i, f in enumerate(fixes, 1)]
    out += ["", "<sub>This does not block the pull request. Set `fail-on-error: true` to make it fail the check.</sub>"]
    return "\n".join(out) + "\n"


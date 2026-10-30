"""Rendering: one normalized review -> GitHub Markdown and structured JSON.

Pure functions. The same review always renders to the same Markdown (no timestamps, stable order).
Counting rule: the report counts **issues** (correlated groups). Supporting checks are shown as a
secondary number. Every count comes from `Review.groups`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .capabilities import Capability
from .correlate import Group, lineage_scope
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
ACTIONABLE = ("blocker", "action_required")
COMMENT_LIMIT = 65_536      # GitHub rejects longer issue comments
SHORTENED = ("<sub>Details were shortened to fit GitHub's comment size limit. "
             "The full review is in the job summary and in review.json.</sub>")


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
    priority: list[Group]               # subset expanded under "Needs attention"
    findings: list[Finding]             # every finding, including hidden ones
    hidden: int = 0                     # findings suppressed from the comment (low confidence, filters)
    coverage: list[dict[str, Any]] = field(default_factory=list)   # per-rule status (JSON only)
    capabilities: list[Capability] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
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


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def severity_counts(groups: list[Group]) -> str:
    return " · ".join(f"{n} {s}" for s in SEVERITIES if (n := sum(g.severity == s for g in groups)))


def not_validated(review: Review) -> str:
    """Why business relationships and definitions were not checked, if they were not."""
    for c in review.capabilities:
        if c.name.startswith("Genie One") and c.status in ("Skipped", "Unavailable"):
            return c.detail
    return ""


def status_key(review: Review) -> str:
    """Machine-readable status: blocker | action_required | review_recommended | informational | no_issues | no_issues_partial."""
    if not review.groups:
        partial = review.access_count or any(c.status == "Unavailable" for c in review.capabilities) or not_validated(review)
        return "no_issues_partial" if partial else "no_issues"
    return min((g.merge_impact for g in review.groups), key=list(STATUS).index)


def status(review: Review) -> str:
    key = status_key(review)
    return {"no_issues": "No issues found", "no_issues_partial": "No issues found in the checks that ran"}.get(key) or STATUS[key]


def outputs(review: Review) -> dict[str, Any]:
    """Action outputs that workflows can branch on."""
    return {"status": status_key(review), "issues": len(review.groups),
            "blockers": sum(g.merge_impact == "blocker" for g in review.groups)}


def _by_category(groups: list[Group]) -> dict[str, list[Group]]:
    out: dict[str, list[Group]] = {}
    for g in groups:  # groups are pre-sorted, so first appearance = highest priority
        out.setdefault(g.category, []).append(g)
    return out


# ------------------------------------------------------------------ finding blocks


def finding_block(g: Group, ctx: LinkContext, facts: dict[str, Any]) -> str:
    f = g.primary
    lines = [f"**{ICON[g.severity]} {_cap(g.severity)} — {f.title}**", "", f.explanation]
    if f.impact:
        label = "Impact" if g.confidence in ("confirmed", "high") else "Possible impact"
        lines += ["", f"**{label}:** {f.impact}"]
    if f.validation:
        lines += ["", f"**To validate:** {f.validation}"]
    if f.suggestion:
        lines += ["", f"**Recommended fix:** {f.suggestion}"]
    sources = list(dict.fromkeys(src for m in g.findings for src in m.detected_by))
    lines += ["", " · ".join([ctx.link(f.file, f.line), " + ".join(sources), CONFIDENCE_LABEL[g.confidence]])]
    lines += ["", evidence_block(g, facts)]
    return "\n".join(lines)


def evidence_block(g: Group, facts: dict[str, Any]) -> str:
    rows = ["<details><summary>Technical evidence</summary>", ""]
    if len(g.findings) > 1:
        rows.append("- Supporting checks: " + "; ".join(f"`{m.check}`: {m.title}" for m in g.findings))
    else:
        rows.append(f"- Check: `{g.primary.check}`")
    rows.append(f"- Rating: {g.severity} severity, {g.confidence} confidence, {g.merge_impact.replace('_', ' ')}")
    scope = lineage_scope(g.findings, facts)
    if scope:
        rows.append(f"- Lineage: {scope}")
    objs = sorted({o for m in g.findings for o in m.affected_objects})
    if objs:
        rows.append(f"- Objects: {', '.join(f'`{o}`' for o in objs)}")
    ev = sorted({e for m in g.findings for e in m.evidence})
    for e in ev:
        g1 = facts.get(e) if e.startswith("ev:genie_one:") else None
        if g1:
            pages = sorted({c.get("label") or "page" for c in g1.get("citations", []) if c.get("kind") == "uc_page" and c.get("relevant") == "yes"})
            objs = sorted({c["ref"].split(".")[-1] for c in g1.get("citations", []) if c.get("kind") == "uc_object"})
            src = "; ".join(x for x in [f"Pages: {', '.join(pages)}" if pages else "", f"tables: {', '.join(objs)}" if objs else "",
                                        f"{len(g1.get('queries') or [])} query result(s)" if g1.get("queries") else ""] if x)
            rows.append(f"- Genie One ({'authoritative' if g1.get('authoritative') else 'unsourced'}): {g1.get('concept')}"
                        + (f" — {src}" if src else ""))
    rows.append("- Evidence: " + ", ".join(f"`{e[:120]}`" for e in ev))
    rows.append(f"- Finding ids: {', '.join(f'`{m.id}`' for m in g.findings)}")
    rows += ["", "</details>"]
    return "\n".join(rows)


def one_line(g: Group, ctx: LinkContext) -> str:
    return (f"- {ICON[g.severity]} **{_cap(g.severity)}** — {g.primary.title} · {ctx.link(g.primary.file, g.primary.line)}"
            f" · details under *{CATEGORIES[g.category]}*")


# ------------------------------------------------------------------ report


def markdown(review: Review, ctx: LinkContext, max_summary_lines: int = 5, compact: bool = False) -> str:
    """The PR comment. `compact` lists non-priority issues as one line each (used when the full report is too long)."""
    out = [MARKER, "# Lakehouse Review", ""]
    summary = "\n".join([l for l in review.summary.strip().splitlines() if l.strip()][:max_summary_lines])
    out += [summary or "No Databricks resources are affected by this pull request.", ""]
    groups = review.groups
    checks = sum(len(g.findings) for g in groups)
    out.append(f"**Review status:** {status(review)}  ")
    if groups:
        line = f"**Issues:** {len(groups)} — {severity_counts(groups)}"
        if checks != len(groups):
            line += f" ({checks} supporting checks)"
        out.append(line)
    gap = not_validated(review)
    if gap:
        out += ["", f"> ℹ️ **Business relationships and definitions were not validated** ({gap}). "
                    "Run with `analysis-depth: detailed` to check joins, grain, and codes against the ontology."]
    if review.access_count:
        out += ["", f"> ⚠️ **Partial review.** The reviewer could not read {_plural(review.access_count, 'object or source')}, "
                    "so some checks were limited. See **Analysis coverage** for the grants to add."]
    out.append("")

    if groups:
        by_cat = _by_category(groups)
        out += ["| Review area | Issues | Highest |", "|---|---:|---|"]
        for c, gs in by_cat.items():
            top = min((g.severity for g in gs), key=SEVERITIES.index)
            out.append(f"| {CATEGORIES[c]} | {len(gs)} | {ICON[top]} {_cap(top)} |")
        out.append("")

        out += ["## Needs attention", ""]
        if review.priority:
            for g in review.priority:
                out += [finding_block(g, ctx, review.facts), ""]
            rest = [g for g in groups if g not in review.priority and g.merge_impact in ACTIONABLE]
            if rest:
                out += [f"**Also needs action ({len(rest)})**", ""]
                out += [one_line(g, ctx) for g in rest]
                out.append("")
        else:
            out += ["Nothing requires action before merging. See the recommendations below.", ""]

        out += ["## Review details", ""]
        if compact:
            out += [SHORTENED, ""]
        for c, gs in by_cat.items():
            top = min((g.severity for g in gs), key=SEVERITIES.index)
            out += [f"<details><summary><b>{CATEGORIES[c]}</b> — {_plural(len(gs), 'issue')} · {_cap(top)}</summary>", ""]
            for g in gs:
                if g in review.priority:
                    out += [f"- {ICON[g.severity]} {g.primary.title} (expanded in **Needs attention**)", ""]
                elif compact:
                    out += [f"- {ICON[g.severity]} **{_cap(g.severity)}** — {g.primary.title} · {ctx.link(g.primary.file, g.primary.line)}", ""]
                else:
                    out += [finding_block(g, ctx, review.facts), ""]
            out += ["</details>", ""]

    out += coverage_section(review)
    if review.hidden:
        out += [f"<sub>{review.hidden} low-confidence or filtered observation(s) are not shown. They are in review.json.</sub>", ""]
    out.append(f"<sub>{review.route_note}</sub>")
    return "\n".join(out).rstrip() + "\n"


def fit_comment(review: Review, ctx: LinkContext, limit: int = COMMENT_LIMIT - 1_000) -> str:
    """The longest report that fits in one PR comment: full, then compact, then cut at a line boundary."""
    body = markdown(review, ctx)
    if len(body) <= limit:
        return body
    body = markdown(review, ctx, compact=True)
    if len(body) <= limit:
        return body
    cut = body[: limit - 500].rsplit("\n", 1)[0]
    unclosed = cut.count("<details>") - cut.count("</details>")
    return cut + "\n\n" + "</details>\n" * max(unclosed, 0) + "\n" + SHORTENED + "\n"


def coverage_section(review: Review) -> list[str]:
    if not (review.capabilities or review.access_md or review.notes):
        return []
    rows = ["<details><summary>Analysis coverage</summary>", ""]
    if review.capabilities:
        rows += ["| Capability | Status | Scope | Findings |", "|---|---|---|---:|"]
        rows += [f"| {c.name} | {c.status} | {cell(c.detail)} | {c.findings} |" for c in review.capabilities]
        rows += ["", "<sub>A finding counts toward every capability it used.</sub>"]
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
        "status_key": status_key(review),
        "blockers": sum(g.merge_impact == "blocker" for g in review.groups),
        "summary": review.summary,
        "issues": len(review.groups),
        "counts": {s: sum(g.severity == s for g in review.groups) for s in SEVERITIES},               # by issue
        "finding_counts": {s: sum(f.severity == s for g in review.groups for f in g.findings) for s in SEVERITIES},
        "groups": [g.to_dict() for g in review.groups],
        "priority": [g.id for g in review.priority],
        "findings": [{**f.to_dict(), "visible": f.id in visible_ids} for f in review.findings],
        "capabilities": [c.to_dict() for c in review.capabilities],
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

"""End-to-end review: impact → evidence → plan → deterministic checks → Genie → publish.

The review always returns something useful. If a stage cannot run (sign-in fails, the bundle does
not resolve, access is missing, no Genie Code model), the comment explains what is missing and how
to fix it, and the rest of the review still runs where it can.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from databricks.sdk import WorkspaceClient

from . import __version__, capabilities, checks, evidence, genie, impact, ontology, prompt, publish, render
from .access import classify
from .correlate import correlate, needs_attention
from .normalize import normalize, visible
from .render import Review
from .config import ConfigError, Settings
from .models import Finding
from .util import group, log, sh, write_json


@dataclass
class Result:
    findings: list[Finding]
    body: str
    route_note: str
    comment_url: str | None = None
    failed: bool = False
    notes: list[str] = field(default_factory=list)


def _publish(s: Settings, body: str, findings: list[Finding], publish_to_github: bool,
             comment: str | None = None, outputs: dict | None = None) -> str | None:
    """Write artifacts, then post `comment` (defaults to `body`) and the full `body` as the job summary."""
    (s.out_dir / "review.md").write_text(body)
    write_json(s.out_dir / "findings.json", [f.to_dict() for f in findings])
    if not publish_to_github:
        return None
    url = publish.post_comment(comment or body)
    publish.step_summary(body)
    publish.set_outputs(findings=len(findings), critical=sum(f.severity == "critical" for f in findings), comment_url=url or "",
                        review_json=str((s.out_dir / "review.json").resolve()),
                        **(outputs or {"status": "not_run", "issues": 0, "blockers": 0}))
    return url


def _problem(s: Settings, title: str, detail: str, fixes: list[str], publish_to_github: bool) -> Result:
    """The review could not run at all: say why and how to fix it."""
    log(f"{title}: {detail}")
    print(f"::warning title=Lakehouse Review::{title}. See the PR comment for how to fix it.")
    body = render.render_problem(title, detail, fixes)
    url = _publish(s, body, [], publish_to_github)
    return Result([], body, "Reviewer: not run.", url, failed=s.fail_on_error, notes=[detail])


def invalid_config(err: ConfigError, out_dir: Path, publish_to_github: bool) -> Result:
    """Inputs or the config file are invalid: list every problem on the PR and fail the check."""
    for p in err.problems:
        print(f"::error title=Lakehouse Review::{p}")
    in_file = err.source not in ("", "defaults")
    fixes = ["Fix action inputs in the workflow file that calls the action, then push again."]
    if in_file:
        fixes.append(f"Fix `{err.source}` on the base branch. It is read from the base branch so a pull request cannot change "
                     "how it is reviewed, so the fix must be merged first (or set `config-file` to another file).")
    body = render.render_problem("The configuration is not valid", "\n".join(f"- {p}" for p in err.problems), fixes, blocking=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "review.md").write_text(body)
    url = None
    if publish_to_github:
        url = publish.post_comment(body)
        publish.step_summary(body)
        publish.set_outputs(status="not_run", issues=0, blockers=0, findings=0, critical=0, comment_url=url or "")
    return Result([], body, "Reviewer: not run.", url, failed=True, notes=err.problems)


class Stop(Exception):
    """A stage could not run: the review ends with this result."""

    def __init__(self, result: Result):
        self.result = result


@dataclass
class GenieStage:
    """What the Genie Code review stage produced."""

    findings: list[Finding] = field(default_factory=list)
    summary: str = ""
    ran: bool = False
    status: str = "not needed"                     # not needed | ran | unavailable
    route_note: str = "AI review engine: not needed. Exact checks only."


def run(s: Settings, client_factory: Callable[[str], WorkspaceClient] = lambda p: WorkspaceClient(profile=p),
        publish_to_github: bool = True) -> Result:
    s.out_dir.mkdir(parents=True, exist_ok=True)
    try:
        w, principal = _sign_in(s, client_factory, publish_to_github)
        imp = _impact(s, publish_to_github)
        ev = _evidence(s, w, imp, principal)
        notes: list[str] = []
        questions, answers = _ontology(s, w, imp, ev, notes)
        genie_one_status = ontology.summary(questions, answers, _ontology_on(s))
        enabled = checks.enabled_checks(s.semantic, s.enable, s.disable)
        plan, det = checks.plan(imp, ev, enabled), checks.deterministic(imp, ev, enabled)
        write_json(s.out_dir / "plan.json", [p.__dict__ for p in plan])
        log(f"{len(plan)} planned checks, {len(det)} deterministic findings")
        stage = GenieStage()
        if plan:
            stage, det, genie_one_status = _genie_review(s, imp, ev, plan, det, answers, genie_one_status, notes, publish_to_github)
    except Stop as stop:
        return stop.result
    return _report(s, imp, ev, plan, enabled, det, stage, genie_one_status, notes, publish_to_github)


def _ontology_on(s: Settings) -> bool:
    return s.semantic != "off"


def _sign_in(s: Settings, client_factory: Callable[[str], WorkspaceClient], publish_to_github: bool) -> tuple[WorkspaceClient, str]:
    try:
        w = client_factory(s.profile)
        principal = w.current_user.me().user_name or "<reviewer-application-id>"
    except Exception as e:  # noqa: BLE001
        raise Stop(_problem(s, "Could not sign in to Databricks",
                            f"Signing in with the reviewer identity failed: {classify(e)}.",
                            ["Check that `databricks-host` is the workspace URL and that the client ID and secret "
                             "(for example `LAKEHOUSE_REVIEW_CLIENT_ID` and `LAKEHOUSE_REVIEW_CLIENT_SECRET`) belong to the same service principal.",
                             "Check that the service principal is added to this workspace and is active.",
                             "If you use OIDC (no secret), add `id-token: write` to the workflow permissions and configure federation for the service principal."],
                            publish_to_github)) from None
    log(f"Signed in as {principal}")
    return w, principal


def _impact(s: Settings, publish_to_github: bool) -> impact.ImpactResult:
    try:
        with group("Analyze: impact"):
            imp = impact.analyze(s.repo, s.base_ref, s.profile, s.target, s.bundle_dir)
            write_json(s.out_dir / "impact.json", imp.graph.to_dict())
            return imp
    except Exception as e:  # noqa: BLE001
        raise Stop(_problem(s, "Could not resolve the bundle",
                            f"`databricks bundle validate` in `{s.bundle_dir}` (target `{s.target or 'default'}`) failed: {str(e)[-500:]}",
                            [f"Check that `bundle-dir` (`{s.bundle_dir}`) contains `databricks.yml` and that `bundle-target` exists.",
                             "The reviewer must be able to read anything the bundle looks up at validate time "
                             "(for example `lookup` variables for warehouses, clusters, or service principals).",
                             "Run `databricks bundle validate -t <target>` locally to see the full error."],
                            publish_to_github)) from None


def _evidence(s: Settings, w: WorkspaceClient, imp: impact.ImpactResult, principal: str) -> evidence.Evidence:
    """Each failed read becomes an access gap, never a crash."""
    with group("Analyze: evidence"):
        try:
            warehouse = evidence.pick_warehouse(w, s.warehouse_id)
        except Exception as e:  # noqa: BLE001
            warehouse = s.warehouse_id
            log(f"Could not list warehouses: {classify(e)}")
        ev = evidence.collect(w, imp, warehouse, principal)
        write_json(s.out_dir / "evidence.json", ev.facts)
        return ev


def _ontology(s: Settings, w: WorkspaceClient, imp: impact.ImpactResult, ev: evidence.Evidence,
              notes: list[str]) -> tuple[list, list]:
    """Plan business questions; Unity Catalog answers what it can; Genie One is asked the rest in detailed mode."""
    if not _ontology_on(s):
        return [], []
    with group("Analyze: ontology"):
        detailed = s.depth == "detailed"
        questions = ontology.plan(imp, ev.facts, s.ontology_budget if detailed else 0)
        answers = []
        if not detailed:
            for q in questions:
                if q.status == "skipped_budget":
                    q.status = "skipped_rapid"
        elif any(q.status == "planned" for q in questions):
            try:
                token = genie._token(s.profile)
                factory = lambda: ontology.GenieOne(w.config.host, token)  # noqa: E731
            except Exception as e:  # noqa: BLE001
                factory = None
                ev.access.genie_one(classify(e))
            answers = ontology.ask(questions, factory, ev.facts)
            for a in answers:
                if a.status == "unavailable":          # permission problems are access gaps
                    ev.access.genie_one(a.error)
                elif a.status == "error":              # anything else is an unresolved question, noted once
                    notes.append(f"Genie One question failed: {a.error[:100]}")
            ev.facts.update(ontology.to_evidence(questions, answers))
        write_json(s.out_dir / "ontology.json", {"questions": [q.to_dict() for q in questions],
                                                 "answers": [a.__dict__ for a in answers]})
        log(f"Ontology: {len(questions)} planned, {len(answers)} asked")
        return questions, answers


def _with_verdicts(status: dict, verdicts: list) -> dict:
    vs = ontology.verdict_summary(verdicts)
    return {**status, "detail": f"{status.get('detail', '')}; {vs}", "verdicts": [v.__dict__ for v in verdicts]} if vs else status


def _genie_review(s: Settings, imp: impact.ImpactResult, ev: evidence.Evidence, plan: list, det: list[Finding],
                  answers: list, genie_one_status: dict, notes: list[str], publish_to_github: bool):
    """Genie Code reviews the planned checks. If no model is reachable, the exact checks still post."""
    stage = GenieStage()
    with group("Analyze: Genie review"):
        try:
            route, route_notes = genie.resolve_route(s)
        except Exception as e:  # noqa: BLE001
            route, route_notes = None, [classify(e)]
        if route is None:
            stage.status = "unavailable"
            ev.access.genie_code(route_notes)
            stage.route_note = "AI review engine: unavailable (no Genie Code model was reachable). Exact checks only."
            verdicts, _ = ontology.reconcile(ev.facts, [], [], publish.clean, Finding)   # nobody judged the answers
            return stage, det, _with_verdicts(genie_one_status, verdicts)

        detailed = s.depth == "detailed"
        facts = ev.facts if detailed else prompt.compact(ev.facts, _referenced_columns(imp))
        try:
            text = prompt.compose(s.base_ref, plan, imp.graph.to_dict(), facts, det, s.instructions,
                                  pr_diff(s, limit=40_000 if detailed else 15_000))
            (s.out_dir / "prompt.md").write_text(text)
            schema = s.out_dir / "findings.schema.json"
            schema.write_text(prompt.resource_text("findings.schema.json"))
            gr = genie.run_review(s, route, text, schema)
        except Exception as e:  # noqa: BLE001
            gr = genie.GenieRun(route, error=classify(e))
        write_json(s.out_dir / "genie_output.json", gr.output or {"error": gr.error})
        stage.ran = gr.output is not None
        stage.status = "ran" if stage.ran else "unavailable"
        if gr.error:
            notes.append(f"Genie review incomplete: {gr.error}")
        stage.findings, gnotes, stage.summary = publish.genie_findings(gr, plan, set(ev.facts))
        notes += gnotes
        if answers:  # every answered ontology question gets a verdict; conflicts cannot be dropped
            verdicts, added = ontology.reconcile(ev.facts, (gr.output or {}).get("ontology_verdicts", []), stage.findings,
                                                 publish.clean, Finding)
            stage.findings += added
            write_json(s.out_dir / "verdicts.json", [v.__dict__ for v in verdicts])
            genie_one_status = _with_verdicts(genie_one_status, verdicts)
        if gr.output and gr.output.get("injection_attempts"):
            notes.append(f"{len(gr.output['injection_attempts'])} possible prompt-injection attempt(s) ignored")
        stage.route_note = (f"Analysis depth: {s.depth}. AI review engine: Genie Code `{route.model}` via {route.label}. "
                            + (f"Genie One: {len(answers)} question(s)." if answers else "Genie One: not called."))
    return stage, det, genie_one_status


def _referenced_columns(imp: impact.ImpactResult) -> dict[str, set[str]]:
    """Columns the code uses, per table: rapid mode keeps only these in the prompt."""
    out: dict[str, set[str]] = {}
    for r in imp.refs.values():
        for t, cols in r.columns.items():
            out.setdefault(t, set()).update(cols)
        for j in r.joins:
            for t, c in (j["left"], j["right"]):
                out.setdefault(t, set()).add(c)
    return out


def _report(s: Settings, imp: impact.ImpactResult, ev: evidence.Evidence, plan: list, enabled: set[str], det: list[Finding],
            stage: GenieStage, genie_one_status: dict, notes: list[str], publish_to_github: bool) -> Result:
    """Normalize, filter, group, and prioritize the findings, then render and publish the review."""
    findings = publish.merge(det, stage.findings, s.severity)
    normalize(findings, ev.facts)
    groups = correlate([f for f in findings if visible(f)], ev.facts)[: s.max_findings]
    review = Review(
        summary=stage.summary or publish.fallback_summary(imp), groups=groups,
        priority=needs_attention(groups), findings=findings,
        hidden=len(findings) - sum(len(g.findings) for g in groups),
        coverage=checks.coverage(plan, findings, enabled, ev, stage.ran),
        capabilities=capabilities.build(imp.graph.to_dict(), ev.facts, findings, ev.gaps, stage.status, genie_one_status),
        facts=ev.facts, access_md=ev.access.render(), access_count=len(ev.access.gaps), route_note=stage.route_note, notes=notes)
    ctx = link_context(s)
    body, comment = render.markdown(review, ctx), render.fit_comment(review, ctx)
    if comment != body:
        log(f"report is {len(body)} characters; the PR comment was shortened to fit GitHub's limit")
    write_json(s.out_dir / "review.json", render.to_json(review, __version__))
    url = _publish(s, body, findings, publish_to_github, comment, render.outputs(review))
    if publish_to_github and s.inline_comments:
        _comment_on_lines(s, groups)
    if ev.access.gaps:
        print(f"::warning title=Lakehouse Review::The reviewer could not read {len(ev.access.gaps)} thing(s). See the PR comment for the grants to add.")
    return Result(findings, body, stage.route_note, url, publish.should_fail(findings, s.fail_on), notes)


def _comment_on_lines(s: Settings, groups: list) -> None:
    """Comment on the code line of each issue that is part of the diff. Never fails the review."""
    lines = commentable_lines(s)
    comments = [{"check": g.primary.check, "path": g.primary.file, "line": g.primary.line, "body": render.inline_body(g)}
                for g in groups if g.primary.line in lines.get(g.primary.file, set())]
    try:
        log(f"{publish.post_inline(comments)} new comment(s) on code lines; {len(groups) - len(comments)} issue(s) are not on a changed line")
    except Exception as e:  # noqa: BLE001
        log(f"Could not comment on code lines: {classify(e)}")


def commentable_lines(s: Settings) -> dict[str, set[int]]:
    """Lines of the head version that GitHub accepts comments on: every line inside a diff hunk."""
    try:
        diff = sh(["git", "diff", "--unified=3", f"{s.base_ref}...HEAD", "--", s.bundle_dir], cwd=s.repo)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, set[int]] = {}
    path = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("@@") and path:
            m = re.match(r"@@ -\S+ \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                out.setdefault(path, set()).update(range(start, start + count))
    return out


def pr_diff(s: Settings, limit: int) -> str:
    """The PR's diff for the bundle folder, capped. Given to Genie Code so it never has to fetch the code itself."""
    try:
        out = sh(["git", "diff", "--unified=3", f"{s.base_ref}...HEAD", "--", s.bundle_dir], cwd=s.repo)
    except Exception:  # noqa: BLE001
        return ""
    return out if len(out) <= limit else out[:limit] + f"\n... diff truncated ({len(out) - limit} more characters); read the files for the rest"


def link_context(s: Settings) -> render.LinkContext:
    """File links point at the PR head commit, so line numbers match the reviewed code."""
    sha = os.environ.get("GITHUB_SHA", "")
    event = os.environ.get("GITHUB_EVENT_PATH")
    if event and os.path.exists(event):
        sha = ((json.load(open(event)).get("pull_request") or {}).get("head") or {}).get("sha") or sha
    return render.LinkContext(os.environ.get("GITHUB_SERVER_URL", ""), os.environ.get("GITHUB_REPOSITORY", ""), sha, s.bundle_dir)

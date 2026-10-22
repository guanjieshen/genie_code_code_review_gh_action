"""End-to-end review: impact → evidence → plan → deterministic checks → Genie → publish.

The review always returns something useful. If a stage cannot run (sign-in fails, the bundle does
not resolve, access is missing, no Genie Code model), the comment explains what is missing and how
to fix it, and the rest of the review still runs where it can.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Callable

from databricks.sdk import WorkspaceClient

from . import __version__, capabilities, checks, evidence, genie, impact, prompt, publish, render
from .access import classify
from .correlate import correlate, needs_attention
from .normalize import Policy, normalize, visible
from .render import Review
from .config import Settings
from .models import Finding
from .util import group, log, write_json


@dataclass
class Result:
    findings: list[Finding]
    body: str
    route_note: str
    comment_url: str | None = None
    failed: bool = False
    notes: list[str] = field(default_factory=list)


def _publish(s: Settings, body: str, findings: list[Finding], publish_to_github: bool) -> str | None:
    (s.out_dir / "review.md").write_text(body)
    write_json(s.out_dir / "findings.json", [f.to_dict() for f in findings])
    if not publish_to_github:
        return None
    url = publish.post_comment(body)
    publish.step_summary(body)
    publish.set_outputs(findings=len(findings), critical=sum(f.severity == "critical" for f in findings), comment_url=url or "")
    return url


def _problem(s: Settings, title: str, detail: str, fixes: list[str], publish_to_github: bool) -> Result:
    """The review could not run at all: say why and how to fix it."""
    log(f"{title}: {detail}")
    print(f"::warning title=Lakehouse Review::{title}. See the PR comment for how to fix it.")
    body = render.render_problem(title, detail, fixes)
    url = _publish(s, body, [], publish_to_github)
    return Result([], body, "Reviewer: not run.", url, failed=s.fail_on_error, notes=[detail])


def run(s: Settings, client_factory: Callable[[str], WorkspaceClient] = lambda p: WorkspaceClient(profile=p),
        publish_to_github: bool = True) -> Result:
    s.out_dir.mkdir(parents=True, exist_ok=True)

    # ---- sign in
    try:
        w = client_factory(s.profile)
        me = w.current_user.me()
        principal = me.user_name or "<reviewer-application-id>"
    except Exception as e:  # noqa: BLE001
        return _problem(s, "Could not sign in to Databricks",
                        f"Signing in with the reviewer identity failed: {classify(e)}.",
                        ["Check that `databricks-host` is the workspace URL and that the client ID and secret "
                         "(for example `GENIE_REVIEWER_CLIENT_ID` and `GENIE_REVIEWER_CLIENT_SECRET`) belong to the same service principal.",
                         "Check that the service principal is added to this workspace and is active.",
                         "If you use OIDC (no secret), add `id-token: write` to the workflow permissions and configure federation for the service principal."],
                        publish_to_github)
    log(f"Signed in as {principal}")

    # ---- impact analysis
    try:
        with group("Analyze: impact"):
            imp = impact.analyze(s.repo, s.base_ref, s.profile, s.target, s.bundle_dir)
            write_json(s.out_dir / "impact.json", imp.graph.to_dict())
    except Exception as e:  # noqa: BLE001
        return _problem(s, "Could not resolve the bundle",
                        f"`databricks bundle validate` in `{s.bundle_dir}` (target `{s.target or 'default'}`) failed: {str(e)[-500:]}",
                        [f"Check that `bundle-dir` (`{s.bundle_dir}`) contains `databricks.yml` and that `bundle-target` exists.",
                         "The reviewer must be able to read anything the bundle looks up at validate time "
                         "(for example `lookup` variables for warehouses, clusters, or service principals).",
                         "Run `databricks bundle validate -t <target>` locally to see the full error."],
                        publish_to_github)

    # ---- evidence (each failed read becomes an access gap, never a crash)
    with group("Analyze: evidence"):
        try:
            warehouse = evidence.pick_warehouse(w, s.warehouse_id)
        except Exception as e:  # noqa: BLE001
            warehouse = s.warehouse_id
            log(f"Could not list warehouses: {classify(e)}")
        ev = evidence.collect(w, imp, warehouse, principal)
        write_json(s.out_dir / "evidence.json", ev.facts)

    enabled = checks.enabled_checks(s.preset, s.semantic, s.enable, s.disable)
    plan = checks.plan(imp, ev, enabled)
    det = checks.deterministic(imp, ev, enabled)
    write_json(s.out_dir / "plan.json", [p.__dict__ for p in plan])
    log(f"{len(plan)} planned checks, {len(det)} deterministic findings")

    # ---- Genie review (optional; exact checks above always post)
    notes: list[str] = []
    genie_ran = False
    genie_code_status = "not needed"
    genie_one_status: dict = {}
    gen: list[Finding] = []
    summary = ""
    route_note = "AI review engine: not needed. Exact checks only."
    if plan:
        with group("Analyze: Genie review"):
            try:
                route, route_notes = genie.resolve_route(s)
            except Exception as e:  # noqa: BLE001
                route, route_notes = None, [classify(e)]
            if route is None:
                genie_code_status = "unavailable"
                ev.access.genie_code(route_notes)
                if s.on_model_unavailable == "fail":
                    return _problem(s, "Genie Code model unavailable", "; ".join(route_notes), [ev.access.gaps[-1].fix], publish_to_github)
                route_note = "AI review engine: unavailable (no Genie Code model was reachable). Exact checks only."
                if s.on_model_unavailable == "skip":
                    det = []
            else:
                use_g1 = s.semantic == "genie-one" and any(p.check.startswith(("semantic.", "governance.")) for p in plan)
                try:
                    text = prompt.compose(s.base_ref, plan, imp.graph.to_dict(), ev.facts, det, s.instructions, use_g1)
                    (s.out_dir / "prompt.md").write_text(text)
                    schema = s.out_dir / "findings.schema.json"
                    schema.write_text(prompt.resource_text("findings.schema.json"))
                    gr = genie.run_review(s, route, text, schema, use_g1)
                except Exception as e:  # noqa: BLE001
                    gr = genie.GenieRun(route, error=classify(e))
                genie_ran = gr.output is not None
                genie_code_status = "ran" if genie_ran else "unavailable"
                genie_one_status = ({"status": "Ran", "detail": f"{len(gr.mcp_questions)} question(s) asked by Genie Code"} if gr.mcp_questions
                                    else {"status": "Not needed", "detail": "Unity Catalog evidence answered the semantic checks"} if use_g1
                                    else {"status": "Not needed", "detail": "no semantic or governance checks planned"})
                if gr.error:
                    notes.append(f"Genie review incomplete: {gr.error}")
                for err in gr.mcp_errors:
                    ev.access.genie_one(err)
                gen, gnotes, summary = publish.genie_findings(gr, plan, set(ev.facts), s.min_confidence)
                notes += gnotes
                if gr.output and gr.output.get("injection_attempts"):
                    notes.append(f"{len(gr.output['injection_attempts'])} possible prompt-injection attempt(s) ignored")
                route_note = (f"AI review engine: Genie Code `{route.model}` via {route.label}. "
                              + (f"Genie One: {len(gr.mcp_questions)} question(s)." if gr.mcp_questions else "Genie One: not called."))
                write_json(s.out_dir / "genie_output.json", gr.output or {"error": gr.error})

    # ---- process results: normalize -> filter -> correlate -> prioritize
    findings = publish.merge(det, gen, s.severity)
    normalize(findings, ev.facts, Policy(s.block_severities, s.block_min_confidence, s.block_categories))
    shown = [f for f in findings if visible(f, s.categories, s.min_severity, s.show_low_confidence, s.include_info)]
    groups = correlate(shown, ev.facts, s.group_related)[: s.max_findings]
    priority = needs_attention(groups, s.max_priority, s.prominent_min_confidence)

    # ---- report
    review = Review(
        summary=summary or publish.fallback_summary(imp), groups=groups, priority=priority, findings=findings,
        hidden=len(findings) - sum(len(g.findings) for g in groups),
        coverage=checks.coverage(plan, findings, enabled, ev, genie_ran),
        capabilities=capabilities.build(imp.graph.to_dict(), ev.facts, findings, ev.gaps, genie_code_status, genie_one_status),
        facts=ev.facts, access_md=ev.access.render(), access_count=len(ev.access.gaps), route_note=route_note, notes=notes)
    body = render.markdown(review, link_context(s))
    write_json(s.out_dir / "review.json", render.to_json(review, __version__))
    url = _publish(s, body, findings, publish_to_github)
    if ev.access.gaps:
        print(f"::warning title=Lakehouse Review::The reviewer could not read {len(ev.access.gaps)} thing(s). See the PR comment for the grants to add.")
    return Result(findings, body, route_note, url, publish.should_fail(findings, s.fail_on), notes)


def link_context(s: Settings) -> render.LinkContext:
    """File links point at the PR head commit, so line numbers match the reviewed code."""
    sha = os.environ.get("GITHUB_SHA", "")
    event = os.environ.get("GITHUB_EVENT_PATH")
    if event and os.path.exists(event):
        sha = ((json.load(open(event)).get("pull_request") or {}).get("head") or {}).get("sha") or sha
    return render.LinkContext(os.environ.get("GITHUB_SERVER_URL", ""), os.environ.get("GITHUB_REPOSITORY", ""), sha, s.bundle_dir)

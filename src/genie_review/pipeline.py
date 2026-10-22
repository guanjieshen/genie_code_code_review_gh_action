"""End-to-end review: impact → evidence → plan → deterministic checks → Genie → publish."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from databricks.sdk import WorkspaceClient

from . import checks, evidence, genie, impact, prompt, publish
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


def run(s: Settings, client_factory: Callable[[str], WorkspaceClient] = lambda p: WorkspaceClient(profile=p),
        publish_to_github: bool = True) -> Result:
    s.out_dir.mkdir(parents=True, exist_ok=True)
    w = client_factory(s.profile)

    with group("Impact analysis"):
        imp = impact.analyze(s.repo, s.base_ref, s.profile, s.target, s.bundle_dir)
        write_json(s.out_dir / "impact.json", imp.graph.to_dict())

    with group("Evidence"):
        warehouse = evidence.pick_warehouse(w, s.warehouse_id)
        ev = evidence.collect(w, imp, warehouse)
        write_json(s.out_dir / "evidence.json", ev.facts)

    enabled = checks.enabled_checks(s.preset, s.semantic, s.enable, s.disable)
    plan = checks.plan(imp, ev, enabled)
    det = checks.deterministic(imp, ev, enabled)
    write_json(s.out_dir / "plan.json", [p.__dict__ for p in plan])
    log(f"{len(plan)} planned checks, {len(det)} deterministic findings")

    notes: list[str] = []
    gen: list[Finding] = []
    summary = ""
    route_note = "Reviewer: deterministic checks only."
    if plan:
        with group("Genie review"):
            route, route_notes = genie.resolve_route(s)
            notes += route_notes
            if route is None:
                if s.on_model_unavailable == "fail":
                    raise SystemExit("Genie Code model unavailable and on-model-unavailable=fail")
                route_note = "Reviewer: deterministic checks only (Genie Code model unavailable: " + "; ".join(route_notes)[:300] + ")."
                if s.on_model_unavailable == "skip":
                    det = []
            else:
                use_g1 = s.semantic == "genie-one" and any(p.check.startswith(("semantic.", "governance.")) for p in plan)
                text = prompt.compose(s.base_ref, plan, imp.graph.to_dict(), ev.facts, det, s.instructions, use_g1)
                (s.out_dir / "prompt.md").write_text(text)
                schema = s.out_dir / "findings.schema.json"
                schema.write_text(prompt.resource_text("findings.schema.json"))
                gr = genie.run_review(s, route, text, schema, use_g1)
                if gr.error:
                    notes.append(f"Genie review incomplete: {gr.error}")
                gen, gnotes, summary = publish.genie_findings(gr, plan, set(ev.facts), s.min_confidence)
                notes += gnotes
                if gr.output and gr.output.get("injection_attempts"):
                    notes.append(f"{len(gr.output['injection_attempts'])} possible prompt-injection attempt(s) ignored")
                route_note = (f"Reviewer: Genie Code `{route.model}` via {route.label}"
                              + ((f"; semantics: Genie One ({len(gr.mcp_questions)} questions)" if gr.mcp_questions
                                 else "; semantics: Genie One available, not needed") if use_g1 else "") + ".")
                write_json(s.out_dir / "genie_output.json", gr.output or {"error": gr.error})

    findings = publish.merge(det, gen, s.severity, s.max_findings)
    cov = checks.coverage(plan, findings, enabled, ev.gaps)
    body = publish.render(imp, findings, summary, cov, route_note, notes, ev.gaps)
    (s.out_dir / "review.md").write_text(body)
    write_json(s.out_dir / "findings.json", [f.to_dict() for f in findings])

    url = None
    if publish_to_github:
        url = publish.post_comment(body)
        publish.step_summary(body)
        publish.set_outputs(findings=len(findings), critical=sum(f.severity == "critical" for f in findings),
                            comment_url=url or "")
    return Result(findings, body, route_note, url, publish.should_fail(findings, s.fail_on), notes)

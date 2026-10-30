"""Command line entry point: `lakehouse-review run` (used by the action), `doctor`, and `setup`."""

from __future__ import annotations

import argparse
import sys

from . import __version__, config, genie, pipeline
from .util import log


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=".")
    p.add_argument("--base-ref", default="origin/main")
    p.add_argument("--profile", required=True, help="Databricks CLI profile for the target workspace")
    p.add_argument("--target", help="Bundle target")
    p.add_argument("--bundle-dir", default=".", help="Bundle folder, relative to the repo root (for monorepos)")
    p.add_argument("--warehouse-id")
    p.add_argument("--semantic", help="genie-one | off")
    p.add_argument("--depth", help="rapid | detailed")
    p.add_argument("--ontology-budget", help="Genie One questions per PR in detailed mode")
    p.add_argument("--ontology", dest="ontology_enabled", choices=["on", "off"], help="Ontology question planning")
    p.add_argument("--reuse-evidence", help="Reuse evidence.json from an earlier run (for A/B comparisons)")
    p.add_argument("--genie-model")
    p.add_argument("--genie-profile", help="Profile for a separate workspace that serves the Genie Code model")
    p.add_argument("--genie-bin", default="genie")
    p.add_argument("--on-model-unavailable", help="deterministic | skip | fail")
    p.add_argument("--fail-on", help="none | critical | high | medium")
    p.add_argument("--fail-on-error", help="true to fail the check when the review cannot run")
    p.add_argument("--config-file")
    p.add_argument("--instructions")
    p.add_argument("--out-dir", default=".lakehouse-review-out")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lakehouse-review", description="Lakehouse Review: Databricks-aware code reviews with Genie-powered semantic analysis.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="Review the current branch against its base")
    _common(run_p)
    run_p.add_argument("--no-publish", action="store_true", help="Write results locally only")
    doc_p = sub.add_parser("doctor", help="Check auth and Genie Code model availability")
    _common(doc_p)
    a = ap.parse_args(argv)

    inputs = {k: v for k, v in vars(a).items() if k not in ("cmd", "no_publish", "reuse_evidence")}
    s = config.load(inputs)

    if a.cmd == "doctor":
        route, notes = genie.resolve_route(s)
        for n in notes:
            print(f"  - {n}")
        print(f"Genie Code: {'OK, ' + route.model + ' via ' + route.label if route else 'UNAVAILABLE'}")
        return 0 if route else 1

    result = pipeline.run(s, publish_to_github=not a.no_publish, reuse_evidence=a.reuse_evidence)
    print(result.body)
    if result.failed:
        log(f"Failing: findings at or above '{s.fail_on}'")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

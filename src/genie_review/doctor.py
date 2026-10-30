"""`lakehouse-review doctor`: check everything the review needs, and say how to fix what is missing.

Run it with the reviewer identity's profile. Each check is independent, so one failure does not hide the rest.
Exit code is 1 only when the review cannot produce useful results (sign-in, bundle, or no Genie Code route
with `on-model-unavailable: fail`); partial setups exit 0 with warnings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from databricks.sdk import WorkspaceClient

from . import bundle as bdl
from . import genie
from .config import Settings
from .evidence import pick_warehouse
from .ontology import GenieOne
from .reviewer_setup import schemas_in_bundle

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class Result:
    name: str
    status: str
    detail: str
    fix: str = ""


def run(s: Settings, client: Callable[[str], WorkspaceClient] = lambda p: WorkspaceClient(profile=p)) -> list[Result]:
    out = [Result("Configuration", OK, f"loaded from {s.config_source}; analysis-depth {s.depth}, fail-on {s.fail_on}")]

    try:
        w = client(s.profile)
        me = w.current_user.me()
        out.append(Result("Sign-in", OK, f"{me.user_name or me.display_name} on {w.config.host}"))
    except Exception as e:  # noqa: BLE001
        out.append(Result("Sign-in", FAIL, str(e).splitlines()[0][:160],
                          "Check databricks-host, client-id, and client-secret (or OIDC federation) for the reviewer."))
        return out

    schemas: set[str] = set()
    try:
        schemas, resolved = schemas_in_bundle(s.repo, s.bundle_dir, s.profile, s.target)
        target = (resolved.get("bundle") or {}).get("target", s.target or "default")
        out.append(Result("Bundle", OK, f"target `{target}` resolves; code uses {len(schemas)} schema(s)"))
        _, state, detail = bdl.deployed_ids(s.repo / s.bundle_dir, s.profile, s.target)
        out.append({
            "deployed": Result("Deployment state", OK, "deployed resources found; drift check can run"),
            "not_deployed": Result("Deployment state", WARN, "target not deployed yet; drift check is skipped until it is"),
            "unreadable": Result("Deployment state", WARN, detail,
                                 "Give the reviewer CAN_READ on the target's workspace.root_path (`lakehouse-review setup`)."),
        }.get(state, Result("Deployment state", WARN, detail or "unknown")))
    except Exception as e:  # noqa: BLE001
        out.append(Result("Bundle", FAIL, str(e).splitlines()[-1][:200],
                          "Run `databricks bundle validate` with the reviewer profile; set bundle-dir and bundle-target if needed."))

    try:
        wh = pick_warehouse(w, s.warehouse_id)
        out.append(Result("SQL warehouse", OK, f"`{wh}`") if wh else
                   Result("SQL warehouse", WARN, "none usable; tag and metric-view checks are skipped",
                          "Give the reviewer CAN_USE on a SQL warehouse and set warehouse-id."))
    except Exception as e:  # noqa: BLE001
        out.append(Result("SQL warehouse", WARN, str(e).splitlines()[0][:160], "Give the reviewer CAN_USE on a SQL warehouse."))

    unreadable = []
    for sch in sorted(schemas):
        try:
            w.schemas.get(sch)
        except Exception:  # noqa: BLE001
            unreadable.append(sch)
    if schemas:
        out.append(Result("Unity Catalog", WARN if unreadable else OK,
                          f"cannot read {', '.join(unreadable)}" if unreadable else f"all {len(schemas)} schema(s) readable",
                          "Run `lakehouse-review setup --principal <application-id>` for the grants." if unreadable else ""))

    route, notes = genie.resolve_route(s)
    if route:
        out.append(Result("Genie Code", OK, f"`{route.model}` via {route.label} ({route.host})"))
    else:
        out.append(Result("Genie Code", FAIL if s.on_model_unavailable == "fail" else WARN, "; ".join(notes)[:300],
                          "Allow the reviewer to use Genie Code through Unity Gateway, set genie-model to a served model, "
                          "or set genie-host to a workspace that serves it. Exact checks still run without it."))

    if s.semantic == "off":
        out.append(Result("Genie One", OK, "disabled (`semantic: off`)"))
    else:
        try:
            tools = GenieOne(w.config.host, genie._token(s.profile)).tools()
            missing = sorted(set(genie.GENIE_ONE_TOOLS) - set(tools))
            out.append(Result("Genie One", WARN if missing else OK,
                              f"missing tools: {', '.join(missing)}" if missing else "reachable",
                              "Update the Genie One MCP server or set semantic: off." if missing else ""))
        except Exception as e:  # noqa: BLE001
            out.append(Result("Genie One", WARN, str(e).splitlines()[0][:160],
                              "Allow service principals to use Genie One, or set semantic: off. Detailed analysis needs it."))
    return out


def render(results: list[Result]) -> str:
    width = max(len(r.name) for r in results)
    lines = []
    for r in results:
        lines.append(f"{r.status:<4}  {r.name:<{width}}  {r.detail}")
        if r.fix and r.status != OK:
            lines.append(f"{'':<4}  {'':<{width}}  Fix: {r.fix}")
    return "\n".join(lines)


def exit_code(results: list[Result]) -> int:
    return 1 if any(r.status == FAIL for r in results) else 0

"""Finding wording, in one place.

Exact checks record structured `params`; the title, description, impact, and recommendation are
rendered from them here. Rules of thumb:
  - title: what is wrong (not just which object)
  - description: the problem and the fact behind it
  - impact: the consequence, qualified when it depends on data the review did not see
  - recommendation: the fix
"""

from __future__ import annotations

import difflib
from typing import Any


def short(fqn: str) -> str:
    """Drop catalog and schema; keep table (and column) names."""
    parts = fqn.split(".")
    return ".".join(parts[2:]) if len(parts) > 3 else parts[-1]


def _code(names: list[str]) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _list(names: list[str], limit: int = 3) -> str:
    shown = _code(names[:limit])
    return shown + (f" and {len(names) - limit} more" if len(names) > limit else "")


def exact(rule: str, p: dict[str, Any]) -> dict[str, str]:
    """Title, description, impact, and recommendation for an exact check. Empty if params are incomplete."""
    try:
        return _exact(rule, p)
    except (KeyError, IndexError, TypeError, ValueError):
        return {}


def _exact(rule: str, p: dict[str, Any]) -> dict[str, str]:
    if rule == "contract.unknown-object":
        t = short(p["table"])
        return dict(
            title=f"Reads missing table `{t}`",
            description=f"`{p['table']}` does not exist in the target workspace, and no task in this PR creates it.",
            impact="The task fails when it reads the table.",
            recommendation=f"Correct the table name, create `{t}` before this task runs, or add the task that produces it.")
    if rule == "contract.column-mismatch":
        t, c, live = short(p["table"]), p["column"], p.get("live", [])
        close = difflib.get_close_matches(c, live, n=1, cutoff=0.4)
        return dict(
            title=f"References missing column `{c}` on `{t}`",
            description=f"`{t}` has no column `{c}`." + (f" The closest existing column is `{close[0]}`." if close else ""),
            impact="The query fails with an unresolved column error.",
            recommendation=(f"Use `{close[0]}`, or add `{c}` to `{t}`." if close else f"Use an existing column of `{t}`, or add `{c}` to it."))
    if rule == "contract.type-mismatch":
        (lt, lc, ltype), (rt, rc, rtype) = p["left"], p["right"]
        string_side = f"{short(rt)}.{rc}" if rtype.lower().startswith("string") else f"{short(lt)}.{lc}"
        return dict(
            title=f"Joins `{lc}` ({ltype}) to `{rc}` ({rtype})",
            description=f"`{short(lt)}.{lc}` is {ltype} and `{short(rt)}.{rc}` is {rtype}, so Spark casts one side implicitly.",
            impact=f"If any `{string_side}` value is not numeric, it becomes NULL and those rows drop out of the join.",
            recommendation=f"Cast both sides to one type explicitly, or align the column types at the source.")
    if rule == "contract.write-schema":
        t = short(p["table"])
        removed, added = p.get("removed", []), p.get("added", [])
        return dict(
            title=f"Drops column {_code(removed)} from existing table `{t}`",
            description=f"`{t}` currently has {_code(removed)}. This PR stops writing it" + (f" and adds {_code(added)}." if added else "."),
            impact=f"Anything that reads {_code(removed)} from `{t}` stops working after deploy.",
            recommendation=f"Write both {_code(removed)} and {_code(added)} during a transition, or migrate every reader in the same change."
                           if added else f"Keep {_code(removed)} until its readers are migrated.")
    if rule == "lineage.downstream-break":
        t, removed, consumers = short(p["table"]), p.get("removed", []), [short(c) for c in p.get("consumers", [])]
        objects = sorted({".".join(c.split(".")[:-1]) or c for c in consumers})
        return dict(
            title=f"Removing {_code(removed)} from `{t}` breaks {_list(objects, 2)}",
            description=f"Column lineage shows {_list(consumers)} reading {_code([f'{t}.{c}' for c in removed])}.",
            impact=f"{_list(objects)} will fail or return wrong results once this deploys.",
            recommendation=f"Update {_list(objects)} to read the new column, or keep {_code(removed)} until every reader is migrated.")
    if rule == "deploy.runtime-support":
        offered = p.get("offered", [])
        return dict(
            title=f"Uses runtime `{p['version']}`, which this workspace does not offer",
            description=f"The target workspace does not list `{p['version']}` as an available Databricks Runtime.",
            impact="The job cannot start a cluster with this runtime.",
            recommendation="Set `spark_version` to a runtime the workspace offers" + (f", for example `{offered[-1]}`." if offered else "."))
    if rule == "deploy.drift":
        fields = p.get("fields", [])
        return dict(
            title=f"Deploy reverts changes made outside the bundle to job `{p['job']}`",
            description=f"The deployed job differs from the bundle on the base branch in {_code(fields)}.",
            impact="Deploying this PR replaces those settings with the bundle's values.",
            recommendation="Add the intended settings to the bundle, or confirm with the job owner that they should be reverted.")
    return {}


# Default fixes for rules whose findings come from the AI review, used when none is given.
DEFAULT_RECOMMENDATION = {
    "semantic.metric-definition-drift": "Compute the metric from its governed definition (metric view or certified dataset), or document why this output differs.",
    "semantic.non-authoritative-source": "Read the authoritative dataset instead of the deprecated or uncertified one.",
    "governance.tagged-data-flow": "Apply the source's classification tags (and any masking or access policy) to the target before writing classified columns.",
}


def validation_note(rule: str, confidence: str, p: dict[str, Any], anchored: bool) -> str:
    """What a developer still needs to check, for findings that are not confirmed."""
    if rule == "contract.type-mismatch" and p.get("left") and p.get("right"):
        (lt, lc, ltype), (rt, rc, rtype) = p["left"], p["right"]
        col = f"{short(rt)}.{rc}" if rtype.lower().startswith("string") else f"{short(lt)}.{lc}"
        return f"Check whether every `{col}` value is numeric. If so, no rows are lost; the explicit cast is still recommended."
    if confidence in ("confirmed", "high"):
        return ""
    if rule == "governance.tagged-data-flow":
        return "Check the target's grants, column masks, and row filters. This review saw classification tags only, not effective access."
    if rule.startswith("semantic.") and not anchored:
        return "Confirm which definition applies. No metric view or certified dataset was found for this output."
    return "Confirm against the cited metadata before acting."


def overlaps(a: str, b: str, threshold: float = 0.6) -> bool:
    """True when `b` mostly repeats `a` (used to drop an impact line that restates the description)."""
    wa, wb = set(a.lower().split()), set(b.lower().split())
    return bool(wb) and len(wa & wb) / len(wb) >= threshold

"""Access diagnostics: turn errors into "what the reviewer could not see, and how to fix it".

The review should always return something useful. When the reviewer identity is missing access,
the comment says which access is missing and gives the exact grant to add.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Gap:
    area: str      # what could not be read, in plain words
    impact: str    # which checks are affected
    fix: str       # exact grant / setting to add (markdown)


@dataclass
class AccessReport:
    principal: str = "<reviewer-application-id>"
    gaps: list[Gap] = field(default_factory=list)

    def add(self, area: str, impact: str, fix: str) -> None:
        if not any(g.area == area for g in self.gaps):
            self.gaps.append(Gap(area, impact, fix))

    # ---- common gaps, with fixes written for the actual reviewer principal

    def table(self, fqn: str) -> None:
        cat, sch, _ = fqn.split(".")
        self.add(f"Metadata for `{fqn}`", "contract, lineage, and governance checks for this table",
                 f"`GRANT USE CATALOG ON CATALOG {cat} TO \\`{self.principal}\\`;` and "
                 f"`GRANT USE SCHEMA, READ METADATA ON SCHEMA {cat}.{sch} TO \\`{self.principal}\\`;` "
                 f"(or `GRANT BROWSE ON CATALOG {cat}`)")

    def lineage(self, fqn: str) -> None:
        cat = fqn.split(".")[0]
        self.add(f"Lineage for `{fqn}`", "`lineage.downstream-break`",
                 f"`GRANT BROWSE ON CATALOG {cat} TO \\`{self.principal}\\`;` (lineage needs at least BROWSE)")

    def job(self, job_id: str) -> None:
        self.add(f"Deployed job `{job_id}`", "`deploy.drift`",
                 f"`databricks permissions update jobs {job_id} --json "
                 f"'{{\"access_control_list\":[{{\"service_principal_name\":\"{self.principal}\",\"permission_level\":\"CAN_VIEW\"}}]}}'`")

    def warehouse(self, warehouse_id: str | None, err: str) -> None:
        target = warehouse_id or "<warehouse-id>"
        self.add("SQL warehouse", "governance tags and metric views (governance and semantic checks)",
                 f"Give the reviewer `CAN_USE` on a SQL warehouse and set `warehouse-id`: "
                 f"`databricks permissions update warehouses {target} --json "
                 f"'{{\"access_control_list\":[{{\"service_principal_name\":\"{self.principal}\",\"permission_level\":\"CAN_USE\"}}]}}'` "
                 f"({err[:100]})")

    def deployed_state(self) -> None:
        self.add("Bundle deployment state", "`deploy.drift` (deployed job ids)",
                 "Deploy the target to a shared `workspace.root_path` and give the reviewer `CAN_READ` on that folder. "
                 "Targets in `mode: development` deploy to a per-user folder the reviewer cannot read.")

    def genie_code(self, notes: list[str]) -> None:
        self.add("Genie Code model", "AI review (semantic and governance findings); exact checks still ran",
                 "Allow the reviewer to use Genie Code through Unity Gateway in this workspace, or set `genie-host` "
                 "to a workspace that serves the Genie Code model. Details: " + "; ".join(notes)[:300])

    def genie_one(self, err: str) -> None:
        self.add("Genie One", "semantic checks that need governed business definitions",
                 f"Allow the reviewer to use the Genie One MCP server, or set `semantic: off`. ({err[:120]})")

    def render(self) -> str:
        if not self.gaps:
            return ""
        rows = ["| What the reviewer could not read | Affects | How to fix |", "|---|---|---|"]
        rows += [f"| {g.area} | {g.impact} | {g.fix} |" for g in self.gaps]
        return "\n".join(rows)


def classify(err: Exception | str) -> str:
    """Short, human label for a Databricks error."""
    s = str(err)
    low = s.lower()
    if "permission" in low or "not authorized" in low or "forbidden" in low or "403" in low or "does not have" in low:
        return "permission denied"
    if "not found" in low or "does not exist" in low or "404" in low:
        return "not found"
    if "auth" in low or "credential" in low or "401" in low or "invalid_client" in low:
        return "authentication failed"
    return s.strip().splitlines()[0][:160] if s.strip() else type(err).__name__

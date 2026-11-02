"""Settings: action inputs override the repo config file, which overrides defaults.

The config file and team instructions are always read from the BASE branch, so a pull request
cannot change how it is reviewed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import SEVERITIES
from .util import CommandError, log, sh

CONFIG_PATH = ".lakehouse-review/config.yml"
FAIL_ON = ["none", "blocker", "critical", "high", "medium"]
KEYS = {"target", "warehouse_id", "fail_on", "analysis_depth", "semantic", "max_findings", "checks", "ontology",
        "instructions_file", "genie"}


class ConfigError(ValueError):
    """Invalid inputs or config file. `problems` lists every issue, so they can all be fixed in one push."""

    def __init__(self, problems: list[str], source: str = ""):
        self.problems, self.source = problems, source
        super().__init__("; ".join(problems))


@dataclass
class Settings:
    repo: Path
    base_ref: str
    profile: str
    out_dir: Path
    bundle_dir: str = "."
    target: str | None = None
    warehouse_id: str | None = None
    depth: str = "rapid"                # rapid | detailed
    semantic: str = "genie-one"         # genie-one | off
    fail_on: str = "none"
    fail_on_error: bool = False         # fail the check when the review cannot run
    max_findings: int = 15
    ontology_budget: int = 6            # Genie One questions per PR in detailed mode
    enable: list[str] = field(default_factory=list)
    disable: list[str] = field(default_factory=list)
    severity: dict[str, str] = field(default_factory=dict)
    instructions: str = ""
    genie_model: str | None = None
    genie_profile: str | None = None    # profile for a separate workspace that serves the Genie Code model
    genie_bin: str = "genie"
    config_source: str = "defaults"


def read_from_base(repo: Path, base_ref: str, path: str) -> str | None:
    try:
        return sh(["git", "show", f"{base_ref}:{path}"], cwd=repo)
    except CommandError:
        return None


def load(inputs: dict[str, Any]) -> Settings:
    """Build Settings from CLI/action inputs plus the base-branch config file."""
    repo = Path(inputs["repo"]).resolve()
    base_ref = inputs["base_ref"]
    bundle_dir = os.path.normpath(inputs.get("bundle_dir") or ".")
    source, cfg = _read_config_file(repo, base_ref, bundle_dir, inputs.get("config_file"))
    problems: list[str] = []

    def pick(key: str, file_key: str, default: Any = None) -> Any:
        """The action input, else the config file value (a dotted path), else the default."""
        if inputs.get(key) not in (None, ""):
            return inputs[key]
        node: Any = cfg
        for part in file_key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        return default if node in (None, "") else node

    def number(key: str, file_key: str, default: int, lo: int, hi: int) -> int:
        v = pick(key, file_key, default)
        if not str(v).isdigit() or not lo <= int(v) <= hi:
            problems.append(f"`{file_key}` must be a whole number from {lo} to {hi}; got {v!r}")
            return default
        return int(v)

    checks = cfg.get("checks") or {}
    if not isinstance(checks, dict):
        problems.append("`checks` must be a mapping with `enable`, `disable`, or `severity`")
        checks = {}
    instructions = ""
    if cfg.get("instructions_file"):
        instructions = (read_from_base(repo, base_ref, os.path.normpath(os.path.join(bundle_dir, cfg["instructions_file"]))) or "").strip()

    s = Settings(
        repo=repo,
        base_ref=base_ref,
        profile=inputs["profile"],
        out_dir=Path(inputs.get("out_dir") or ".lakehouse-review-out").resolve(),
        bundle_dir=bundle_dir,
        target=pick("target", "target"),
        warehouse_id=pick("warehouse_id", "warehouse_id"),
        depth=str(pick("depth", "analysis_depth", "rapid")),
        semantic=str(pick("semantic", "semantic", "genie-one")),
        fail_on=str(pick("fail_on", "fail_on", "none")),
        fail_on_error=str(inputs.get("fail_on_error") or "false").lower() == "true",
        max_findings=number("max_findings", "max_findings", 15, 1, 100),
        ontology_budget=number("ontology_budget", "ontology.max_questions", 6, 0, 20),
        enable=list(checks.get("enable") or []),
        disable=list(checks.get("disable") or []),
        severity=dict(checks.get("severity") or {}),
        instructions=instructions,
        genie_model=pick("genie_model", "genie.model"),
        genie_profile=inputs.get("genie_profile") or None,
        genie_bin=inputs.get("genie_bin") or "genie",
        config_source=source,
    )
    problems += validate(s)
    if problems:
        raise ConfigError(problems, source)
    return s


def validate(s: Settings) -> list[str]:
    """Every problem with the merged settings, worded for the person who has to fix them."""
    p: list[str] = []

    def one_of(name: str, value: Any, allowed: list[str]) -> None:
        if value not in allowed:
            p.append(f"`{name}` must be one of {', '.join(allowed)}; got {value!r}")

    one_of("fail-on", s.fail_on, FAIL_ON)
    one_of("analysis-depth", s.depth, ["rapid", "detailed"])
    one_of("semantic", s.semantic, ["genie-one", "off"])
    if os.path.isabs(s.bundle_dir) or s.bundle_dir == ".." or s.bundle_dir.startswith("../"):
        p.append(f"`bundle-dir` must be a folder inside the repository; got {s.bundle_dir!r}")
    for check, sev in s.severity.items():
        one_of(f"checks.severity.{check}", sev, SEVERITIES)
    return p


def _read_config_file(repo: Path, base_ref: str, bundle_dir: str, explicit: str | None) -> tuple[str, dict[str, Any]]:
    """The config file from the base branch: (path, settings), or ("defaults", {}) if there is none.

    Paths in the config file, and the default location, are relative to the bundle folder.
    """
    path = explicit or os.path.normpath(os.path.join(bundle_dir, CONFIG_PATH))
    raw = read_from_base(repo, base_ref, path)
    if raw is None:
        if explicit:
            raise ConfigError([f"config file `{path}` was not found on the base branch `{base_ref}`"], path)
        return "defaults", {}
    try:
        cfg = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        raise ConfigError([f"`{path}` is not valid YAML: {str(e).splitlines()[0]}"], path) from None
    if not isinstance(cfg, dict):
        raise ConfigError([f"`{path}` must be a mapping of settings, not a {type(cfg).__name__}"], path)
    unknown = sorted(set(cfg) - KEYS)
    if unknown:
        raise ConfigError([f"`{path}` has unknown settings: {', '.join(unknown)}. Valid settings: {', '.join(sorted(KEYS))}"], path)
    log(f"Loaded {path} from {base_ref}")
    return path, cfg

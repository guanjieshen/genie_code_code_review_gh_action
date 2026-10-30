"""Settings: action inputs override the repo config file, which overrides defaults.

The repo config file and team instructions are always read from the BASE branch, so a pull
request cannot change how it is reviewed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import CATEGORIES, CONFIDENCE, SEVERITIES
from .util import CommandError, log, sh

DEFAULT_CONFIG_PATH = ".lakehouse-review/config.yml"
LEGACY_CONFIG_PATH = ".genie-review/config.yml"   # read if the new path is absent
FAIL_ON = ["none", "blocker", "critical", "high", "medium"]
ON_MODEL_UNAVAILABLE = ["deterministic", "skip", "fail"]
KNOWN_KEYS = {"fail_on", "analysis_depth", "semantic", "preset", "target", "warehouse_id", "instructions_file",
              "genie", "limits", "checks", "report", "merge_policy", "ontology"}


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
    target: str | None = None
    warehouse_id: str | None = None
    preset: str = "core"
    semantic: str = "genie-one"  # genie-one | genie-agent:<space_id> | off
    genie_model: str | None = None
    genie_profile: str | None = None  # profile for the workspace that serves the Genie Code model
    genie_bin: str = "genie"
    on_model_unavailable: str = "deterministic"
    fail_on: str = "none"
    fail_on_error: bool = False  # fail the check when the review cannot run (setup problems)
    max_findings: int = 15
    min_confidence: float = 0.6
    instructions: str = ""
    enable: list[str] = field(default_factory=list)
    disable: list[str] = field(default_factory=list)
    severity: dict[str, str] = field(default_factory=dict)
    config_source: str = "defaults"
    # ---- report (what the PR comment shows; the JSON keeps everything)
    categories: list[str] = field(default_factory=lambda: ["contracts", "correctness", "semantics", "lineage", "governance", "deployment"])
    min_severity: str = "low"
    prominent_min_confidence: str = "high"   # Needs attention requires at least this confidence
    show_low_confidence: bool = False
    include_info: bool = False
    group_related: bool = True
    max_priority: int = 3
    # ---- analysis depth and ontology
    depth: str = "rapid"             # rapid | detailed
    ontology: bool = True            # plan ontology questions (asked only in detailed mode)
    ontology_budget: int = 6         # Genie One questions per PR in detailed mode
    # ---- merge-impact policy
    block_severities: list[str] = field(default_factory=lambda: ["critical"])
    block_min_confidence: str = "confirmed"
    block_categories: list[str] = field(default_factory=lambda: ["contracts", "lineage", "deployment"])
    bundle_dir: str = "."

    @property
    def model_profile(self) -> str:
        return self.genie_profile or self.profile


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
    # Paths in the config file (and the default config location) are relative to the bundle folder
    config_path = inputs.get("config_file") or os.path.normpath(os.path.join(bundle_dir, DEFAULT_CONFIG_PATH))

    file_cfg: dict[str, Any] = {}
    raw = read_from_base(repo, base_ref, config_path)
    if raw is None and not inputs.get("config_file"):
        legacy = os.path.normpath(os.path.join(bundle_dir, LEGACY_CONFIG_PATH))
        raw = read_from_base(repo, base_ref, legacy)
        if raw is not None:
            config_path = legacy
            log(f"Using {legacy}; rename the folder to .lakehouse-review/ (the old name will stop working in v1.0)")
    if raw:
        try:
            file_cfg = yaml.safe_load(raw) or {}
        except yaml.YAMLError as e:
            raise ConfigError([f"`{config_path}` is not valid YAML: {str(e).splitlines()[0]}"], config_path) from None
        if not isinstance(file_cfg, dict):
            raise ConfigError([f"`{config_path}` must be a mapping of settings, not a {type(file_cfg).__name__}"], config_path)
        log(f"Loaded {config_path} from {base_ref}")
        unknown = sorted(set(file_cfg) - KNOWN_KEYS)
        if unknown:
            log(f"Ignoring unknown keys in {config_path}: {', '.join(unknown)}")
    elif inputs.get("config_file"):
        raise ConfigError([f"config file `{config_path}` was not found on the base branch `{base_ref}`"], config_path)

    def pick(key: str, file_key: str | None = None, default: Any = None) -> Any:
        v = inputs.get(key)
        if v not in (None, ""):
            return v
        for path in filter(None, [file_key, key]):
            node: Any = file_cfg
            for part in path.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if node not in (None, ""):
                return node
        return default

    problems: list[str] = []

    def num(kind: type, key: str, file_key: str, default: Any, lo: float, hi: float) -> Any:
        v = pick(key, file_key, default)
        try:
            n = kind(v)
        except (TypeError, ValueError):
            problems.append(f"`{file_key}` must be a number, got {v!r}")
            return default
        if not lo <= n <= hi:
            problems.append(f"`{file_key}` must be between {lo} and {hi}, got {n}")
            return default
        return n

    checks = file_cfg.get("checks") or {}
    if not isinstance(checks, dict):
        problems.append("`checks` must be a mapping with `enable`, `disable`, or `severity`")
        checks = {}
    instructions = []
    instr_file = file_cfg.get("instructions_file")
    if instr_file:
        text = read_from_base(repo, base_ref, os.path.normpath(os.path.join(bundle_dir, instr_file)))
        if text:
            instructions.append(text.strip())
    if inputs.get("instructions"):
        instructions.append(str(inputs["instructions"]).strip())

    s = Settings(
        repo=repo,
        base_ref=base_ref,
        profile=inputs["profile"],
        out_dir=Path(inputs.get("out_dir") or ".lakehouse-review-out").resolve(),
        target=pick("target"),
        warehouse_id=pick("warehouse_id"),
        preset=pick("preset", default="core"),
        semantic=pick("semantic", default="genie-one"),
        genie_model=pick("genie_model", "genie.model"),
        genie_profile=inputs.get("genie_profile") or None,
        genie_bin=inputs.get("genie_bin") or "genie",
        on_model_unavailable=pick("on_model_unavailable", "genie.on_model_unavailable", default="deterministic"),
        fail_on=pick("fail_on", "limits.fail_on", default="none"),
        fail_on_error=str(pick("fail_on_error", default="false")).lower() == "true",
        depth=str(pick("depth", "analysis_depth", default="rapid")),
        ontology=_bool(pick("ontology_enabled", "ontology.enabled", default=True)),
        ontology_budget=num(int, "ontology_budget", "ontology.max_questions", 6, 0, 20),
        max_findings=num(int, "max_findings", "limits.max_findings", 15, 1, 100),
        min_confidence=num(float, "min_confidence", "limits.min_confidence", 0.6, 0, 1),
        instructions="\n\n".join(instructions),
        enable=list(checks.get("enable") or []),
        disable=list(checks.get("disable") or []),
        severity=dict(checks.get("severity") or {}),
        config_source=config_path if raw else "defaults",
        **_report_settings(file_cfg),
        bundle_dir=bundle_dir,
    )
    problems += validate(s)
    if problems:
        raise ConfigError(problems, s.config_source)
    return s


def validate(s: Settings) -> list[str]:
    """Every problem with the merged settings, worded for the person who has to fix them."""
    p: list[str] = []

    def one_of(name: str, value: Any, allowed: list[str]) -> None:
        if value not in allowed:
            p.append(f"`{name}` must be one of {', '.join(allowed)}; got {value!r}")

    one_of("fail-on", s.fail_on, FAIL_ON)
    one_of("analysis-depth", s.depth, ["rapid", "detailed"])
    one_of("on-model-unavailable", s.on_model_unavailable, ON_MODEL_UNAVAILABLE)
    if s.semantic not in ("genie-one", "off") and not str(s.semantic).startswith("genie-agent:"):
        p.append(f"`semantic` must be genie-one or off; got {s.semantic!r}")
    bd = s.bundle_dir
    if os.path.isabs(bd) or bd == ".." or bd.startswith("../"):
        p.append(f"`bundle-dir` must be a folder inside the repository; got {bd!r}")
    one_of("report.min_severity", s.min_severity, SEVERITIES)
    one_of("report.prominent_min_confidence", s.prominent_min_confidence, CONFIDENCE)
    one_of("merge_policy.block_min_confidence", s.block_min_confidence, CONFIDENCE)
    for name, values, allowed in (("report.categories", s.categories, list(CATEGORIES)),
                                  ("merge_policy.block_categories", s.block_categories, list(CATEGORIES)),
                                  ("merge_policy.block_severities", s.block_severities, SEVERITIES)):
        bad = [v for v in values if v not in allowed]
        if bad:
            p.append(f"`{name}` has unknown value(s) {', '.join(map(str, bad))}; use {', '.join(allowed)}")
    for check, sev in s.severity.items():
        if sev not in SEVERITIES:
            p.append(f"`checks.severity.{check}` must be one of {', '.join(SEVERITIES)}; got {sev!r}")
    if not 0 <= s.max_priority <= 10:
        p.append(f"`report.max_priority` must be between 0 and 10; got {s.max_priority}")
    return p


def _bool(v: Any) -> bool:
    return str(v).lower() in ("1", "true", "yes", "on")  # "off" -> False


def _report_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """`report:` and `merge_policy:` blocks of the config file. Unset keys keep their defaults."""
    r, m, out = cfg.get("report") or {}, cfg.get("merge_policy") or {}, {}
    for key in ("categories",):
        if r.get(key):
            out[key] = list(r[key])
    for key in ("min_severity", "prominent_min_confidence"):
        if r.get(key):
            out[key] = str(r[key])
    for key in ("show_low_confidence", "include_info", "group_related"):
        if key in r:
            out[key] = _bool(r[key])
    if r.get("max_priority") is not None:
        v = r["max_priority"]
        out["max_priority"] = int(v) if str(v).isdigit() else -1   # -1 is reported by validate()
    if m.get("block_severities"):
        out["block_severities"] = list(m["block_severities"])
    if m.get("block_min_confidence"):
        out["block_min_confidence"] = str(m["block_min_confidence"])
    if m.get("block_categories"):
        out["block_categories"] = list(m["block_categories"])
    return out

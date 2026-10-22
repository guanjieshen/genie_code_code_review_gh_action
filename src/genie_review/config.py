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

from .util import CommandError, log, sh

DEFAULT_CONFIG_PATH = ".genie-review/config.yml"
FAIL_ON = ["none", "critical", "high", "medium"]
ON_MODEL_UNAVAILABLE = ["deterministic", "skip", "fail"]


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
    max_findings: int = 15
    min_confidence: float = 0.6
    instructions: str = ""
    enable: list[str] = field(default_factory=list)
    disable: list[str] = field(default_factory=list)
    severity: dict[str, str] = field(default_factory=dict)
    config_source: str = "defaults"
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
    if raw:
        file_cfg = yaml.safe_load(raw) or {}
        log(f"Loaded {config_path} from {base_ref}")

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

    checks = file_cfg.get("checks") or {}
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
        out_dir=Path(inputs.get("out_dir") or ".genie-review-out").resolve(),
        target=pick("target"),
        warehouse_id=pick("warehouse_id"),
        preset=pick("preset", default="core"),
        semantic=pick("semantic", default="genie-one"),
        genie_model=pick("genie_model", "genie.model"),
        genie_profile=inputs.get("genie_profile") or None,
        genie_bin=inputs.get("genie_bin") or "genie",
        on_model_unavailable=pick("on_model_unavailable", "genie.on_model_unavailable", default="deterministic"),
        fail_on=pick("fail_on", "limits.fail_on", default="none"),
        max_findings=int(pick("max_findings", "limits.max_findings", default=15)),
        min_confidence=float(pick("min_confidence", "limits.min_confidence", default=0.6)),
        instructions="\n\n".join(instructions),
        enable=list(checks.get("enable") or []),
        disable=list(checks.get("disable") or []),
        severity=dict(checks.get("severity") or {}),
        config_source=config_path if raw else "defaults",
        bundle_dir=bundle_dir,
    )
    if s.fail_on not in FAIL_ON:
        raise ValueError(f"fail-on must be one of {FAIL_ON}, got {s.fail_on!r}")
    if s.on_model_unavailable not in ON_MODEL_UNAVAILABLE:
        raise ValueError(f"on-model-unavailable must be one of {ON_MODEL_UNAVAILABLE}")
    return s

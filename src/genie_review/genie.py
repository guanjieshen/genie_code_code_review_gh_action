"""Genie Code CLI integration: route resolution, headless config, sanitized workspace, and review run.

All model calls go through Genie Code (and Genie One MCP for semantics). The action never calls
Unity Gateway model endpoints directly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from databricks.sdk import WorkspaceClient

from .config import Settings
from .util import CommandError, log, sh

DEFAULT_GENIE_MODEL = "gpt-5.6-sol"
GENIE_ONE_TOOLS = ["genie_ask", "genie_poll_response", "genie_get_query_result"]
# Agent configuration Genie loads from a repo. Replaced with base-branch versions before review.
AGENT_CONFIG_PATHS = ["AGENTS.md", "AGENTS.override.md", ".agents", ".genie", ".codex", ".genie-review"]


@dataclass
class Route:
    profile: str
    host: str
    workspace_id: str
    model: str
    label: str


@dataclass
class GenieRun:
    route: Route | None
    output: dict[str, Any] | None = None
    mcp_questions: list[str] = field(default_factory=list)
    mcp_errors: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _client(profile: str) -> WorkspaceClient:
    return WorkspaceClient(profile=profile)


def _token(profile: str) -> str:
    return json.loads(sh(["databricks", "auth", "token", "-p", profile, "-o", "json"]))["access_token"]


def write_home(home: Path, route: Route, genie_one_host: str | None) -> None:
    """Write the same config Genie's interactive onboarding writes, plus an allowlisted Genie One MCP server."""
    home.mkdir(parents=True, exist_ok=True)
    lines = [
        'model_provider = "databricks"',
        f'model = "{route.model}"',
        f'databricks_profile = "{route.profile}"',
        "",
        "[model_providers.databricks]",
        'name = "Unity Gateway"',
        f'base_url = "{route.host}/ai-gateway/codex/v1"',
        'wire_api = "responses"',
        "",
        "[model_providers.databricks.auth]",
        'command = "databricks"',
        f'args = ["auth", "token", "--profile", "{route.profile}", "--timeout", "15s", "--output", "json"]',
        "timeout_ms = 60000",
        "refresh_interval_ms = 900000",
        "",
        "[model_providers.databricks.http_headers]",
        f'x-databricks-workspace-id = "{route.workspace_id}"',
    ]
    if genie_one_host:
        lines += [
            "",
            "[mcp_servers.genie_one]",
            f'url = "{genie_one_host}/ai-gateway/mcp-services/system.ai.genie_one_mcp"',
            'bearer_token_env_var = "GENIE_ONE_TOKEN"',
            "enabled_tools = " + json.dumps(GENIE_ONE_TOOLS),
            'default_tools_approval_mode = "approve"',
            "tool_timeout_sec = 300",
        ]
    (home / "config.toml").write_text("\n".join(lines) + "\n")


def _route_for(profile: str, model: str, label: str) -> Route:
    w = _client(profile)
    return Route(profile, w.config.host.rstrip("/"), str(w.get_workspace_id()), model, label)


def resolve_route(s: Settings) -> tuple[Route | None, list[str]]:
    """Try routes in order and return the first that answers. Each probe is a tiny Genie Code call."""
    model = s.genie_model or DEFAULT_GENIE_MODEL
    candidates = [(s.profile, model, "target workspace")]
    if s.genie_profile and s.genie_profile != s.profile:
        candidates.append((s.genie_profile, model, "model workspace"))
    notes = []
    for profile, m, label in candidates:
        try:
            route = _route_for(profile, m, label)
        except Exception as e:  # noqa: BLE001
            notes.append(f"{label}: cannot connect ({str(e)[:100]})")
            continue
        ok, why = probe(s.genie_bin, route)
        if ok:
            log(f"Genie Code route: {m} via {label} ({route.host})")
            return route, notes
        notes.append(f"{label}: model `{m}` unavailable ({why})")
        log(notes[-1])
    return None, notes


def probe(genie_bin: str, route: Route) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        write_home(home, route, None)
        try:
            r = subprocess.run([genie_bin, "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "Reply with exactly: OK"],
                               cwd=tmp, env={**os.environ, "GENIE_HOME": str(home)}, capture_output=True, text=True,
                               timeout=180, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return False, "timed out"
        except FileNotFoundError:
            return False, f"Genie Code CLI not found at {genie_bin}"
        text = (r.stdout + r.stderr)
        if r.returncode == 0 and "OK" in r.stdout:
            return True, ""
        err = next((l for l in text.splitlines() if "error" in l.lower()), text.strip()[-200:])
        return False, err[:200]


def sanitized_worktree(repo: Path, base_ref: str, dest: Path, bundle_dir: str = ".") -> Path:
    """A detached worktree at HEAD whose agent-config paths are replaced with the base branch's versions."""
    sh(["git", "worktree", "add", "--detach", str(dest), "HEAD"], cwd=repo)
    rules = [p for p in sh(["git", "ls-files", "*.rules"], cwd=dest).splitlines() if p]
    nested = [p for p in sh(["git", "ls-files", "*AGENTS.md", "*AGENTS.override.md"], cwd=dest).splitlines() if p]
    for rel in AGENT_CONFIG_PATHS + rules + nested + [os.path.join(bundle_dir, p) for p in AGENT_CONFIG_PATHS if bundle_dir != "."]:
        target = dest / rel
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        try:
            sh(["git", "checkout", base_ref, "--", rel], cwd=dest)
            sh(["git", "reset", "-q", "--", rel], cwd=dest, check=False)
        except CommandError:
            pass  # not present on base: stays removed
    return dest


def run_review(s: Settings, route: Route, prompt: str, schema_path: Path, use_genie_one: bool) -> GenieRun:
    run = GenieRun(route)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        home = tmp_p / "home"
        write_home(home, route, _client(s.profile).config.host.rstrip("/") if use_genie_one else None)
        tree = sanitized_worktree(s.repo, s.base_ref, tmp_p / "tree", s.bundle_dir)
        out_file = tmp_p / "out.json"
        env = {**os.environ, "GENIE_HOME": str(home)}
        if use_genie_one:
            env["GENIE_ONE_TOKEN"] = _token(s.profile)
        try:
            r = subprocess.run([s.genie_bin, "exec", "--strict-config", "--ephemeral", "--json", "-s", "read-only",
                                "--output-schema", str(schema_path), "-o", str(out_file), "-"],
                               cwd=tree, env=env, input=prompt, capture_output=True, text=True, timeout=1500)
        except subprocess.TimeoutExpired:
            run.error = "Genie review timed out"
            return run
        finally:
            sh(["git", "worktree", "remove", "--force", str(tree)], cwd=s.repo, check=False)
        (s.out_dir / "genie.events.jsonl").write_text(r.stdout)
        for line in r.stdout.splitlines():
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = evt.get("item") or {}
            if evt.get("type") == "item.completed" and item.get("type") == "mcp_tool_call" and item.get("tool") == "genie_ask":
                if item.get("status") == "completed":
                    run.mcp_questions.append((item.get("arguments") or {}).get("question", ""))
                else:
                    run.mcp_errors.append(str((item.get("error") or {}).get("message", item.get("status")))[:200])
            if evt.get("type") == "turn.completed":
                run.usage = evt.get("usage") or {}
            if evt.get("type") == "turn.failed":
                run.error = str((evt.get("error") or {}).get("message", "turn failed"))[:300]
        if out_file.exists():
            text = out_file.read_text().strip().removeprefix("```json").removesuffix("```").strip()
            try:
                run.output = json.loads(text)
            except json.JSONDecodeError:
                run.error = run.error or "Genie returned output that is not valid JSON"
        elif not run.error:
            run.error = f"Genie exited with {r.returncode}: {r.stderr.strip()[-300:]}"
    return run

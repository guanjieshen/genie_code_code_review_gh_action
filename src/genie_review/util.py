"""Small helpers shared across modules."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


class CommandError(RuntimeError):
    pass


def sh(cmd: list[str], cwd: Path | str | None = None, check: bool = True, env: dict | None = None,
       input_text: str | None = None, timeout: int | None = None) -> str:
    """Run a command and return stdout. Raises CommandError with stderr on failure when check=True."""
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env, input=input_text, timeout=timeout)
    if check and r.returncode != 0:
        raise CommandError(f"{' '.join(cmd[:4])} ... failed ({r.returncode}): {r.stderr.strip()[-1200:]}")
    return r.stdout


def log(msg: str) -> None:
    print(f"[lakehouse-review] {msg}", file=sys.stderr, flush=True)


def group(title: str):
    """GitHub Actions log group (no-op formatting outside Actions)."""
    class _G:
        def __enter__(self):
            print(f"::group::{title}", file=sys.stderr, flush=True)

        def __exit__(self, *a):
            print("::endgroup::", file=sys.stderr, flush=True)
    return _G()


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))


def is_empty(v: Any) -> bool:
    return v is None or v == {} or v == [] or v == ""

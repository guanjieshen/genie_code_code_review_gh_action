"""Print an access token for a Databricks CLI profile, in the format of `databricks auth token -o json`.

Genie Code calls this as its auth command. `databricks auth token` only supports user (U2M) logins;
this uses the Databricks SDK, so it also works for service principals (M2M) and GitHub OIDC.

    python -m genie_review.token --profile genie-review
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

from databricks.sdk import WorkspaceClient


def access_token(profile: str) -> str:
    headers = WorkspaceClient(profile=profile).config.authenticate()
    return headers["Authorization"].split(" ", 1)[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    a = ap.parse_args(argv)
    try:
        tok = access_token(a.profile)
    except Exception as e:  # noqa: BLE001
        print(f"genie-review token: {e}", file=sys.stderr)
        return 1
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat()
    print(json.dumps({"access_token": tok, "token_type": "Bearer", "expiry": expiry}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

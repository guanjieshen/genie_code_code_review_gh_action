"""action.yml: the input validation step, and docs that must stay in sync with it."""

import os
import re
import subprocess
from pathlib import Path

import yaml

from genie_review.checks import CATALOG

ROOT = Path(__file__).resolve().parents[1]
ACTION = yaml.safe_load((ROOT / "action.yml").read_text())


def _validate(tmp_path, **env):
    script = next(s for s in ACTION["runs"]["steps"] if s["name"] == "Initialize: validate inputs")["run"]
    ws = tmp_path / "ws"
    (ws / "app").mkdir(parents=True, exist_ok=True)
    (ws / "app/databricks.yml").write_text("bundle: {name: x}\n")
    base = {"PATH": os.environ["PATH"], "GITHUB_WORKSPACE": str(ws), "RUNNER_TEMP": str(tmp_path), "GITHUB_ENV": str(tmp_path / "env"),
            "HOST": "https://adb-1.azuredatabricks.net", "CLIENT_ID": "id", "BUNDLE_DIR": "app", "GENIE_VERSION": "v0.2.0",
            "FAIL_ON_ERROR": "false", "UPLOAD": "false"}
    r = subprocess.run(["bash", "-c", script], env={**base, **env}, capture_output=True, text=True)
    report = tmp_path / "lakehouse-review-setup-error.md"
    return r.returncode, report.read_text() if report.exists() else ""


def test_valid_inputs_pass(tmp_path):
    assert _validate(tmp_path) == (0, "")


def test_every_bad_input_is_reported_at_once(tmp_path):
    code, report = _validate(tmp_path, HOST="http://x", CLIENT_ID="", DEPTH="deep", FAIL_ON="low",
                             SEMANTIC="maybe", GENIE_VERSION="latest", BUNDLE_DIR="../x", CA_BUNDLE="missing.pem")
    assert code == 1
    for name in ("databricks-host", "client-id", "analysis-depth", "fail-on", "semantic", "genie-version", "bundle-dir", "ca-bundle"):
        assert f"`{name}`" in report, name


def test_missing_bundle_is_explained(tmp_path):
    code, report = _validate(tmp_path, BUNDLE_DIR=".")
    assert code == 1 and "fetch-depth: 0" in report


def test_ca_bundle_is_exported_to_every_tool(tmp_path):
    pem = tmp_path / "ca.pem"
    pem.write_text("-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n")
    assert _validate(tmp_path, CA_BUNDLE=str(pem))[0] == 0
    exported = (tmp_path / "env").read_text()
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "GENIE_CA_CERTIFICATE", "NODE_EXTRA_CA_CERTS"):
        assert f"{var}={pem}" in exported


def test_inputs_doc_lists_every_input_and_output():
    doc = (ROOT / "docs/inputs.md").read_text()
    for name in [*ACTION["inputs"], *ACTION["outputs"]]:
        assert re.search(rf"^\| `{re.escape(name)}` \|", doc, re.M), f"{name} missing from docs/inputs.md"


def test_validated_enums_match_the_python_config():
    from genie_review import config
    script = next(s for s in ACTION["runs"]["steps"] if s["name"] == "Initialize: validate inputs")["run"]
    assert f'one_of fail-on "$FAIL_ON" {" ".join(config.FAIL_ON)}' in script
    assert f'one_of on-model-unavailable "$ON_MODEL_UNAVAILABLE" {" ".join(config.ON_MODEL_UNAVAILABLE)}' in script


def test_configuration_doc_lists_every_check():
    doc = (ROOT / "docs/configuration.md").read_text()
    for check in CATALOG:
        assert f"`{check}`" in doc, check


def test_docs_have_no_internal_hosts():
    # Split so the CI guard does not match this file.
    pattern = re.compile("pypi-" "proxy|" r"\.dev\." r"databricks\.com|" r"\.staging\." r"cloud\.databricks|@" r"databricks\.com")
    for p in [ROOT / "README.md", *ROOT.glob("docs/*.md"), *ROOT.glob("examples/**/*.md")]:
        assert not pattern.search(p.read_text()), p

#!/usr/bin/env bash
# Installs the Genie Code CLI release $GENIE_VERSION into $RUNNER_TEMP/genie-cli and adds it to the job PATH.
# Used by action.yml and the integration workflow.
set -euo pipefail
case "$(uname -m)" in
  x86_64) arch=x86_64 ;;
  aarch64|arm64) arch=aarch64 ;;
  *) echo "::error::Unsupported runner architecture $(uname -m)"; exit 1 ;;
esac
case "$(uname -s)" in
  Linux) target="${arch}-unknown-linux-musl" ;;
  Darwin) target="${arch}-apple-darwin" ;;
  *) echo "::error::Unsupported runner OS $(uname -s)"; exit 1 ;;
esac
pkg="genie-package-${target}.tar.gz"
base="https://github.com/databricks/genie-code-cli/releases/download/${GENIE_VERSION}"
dir="${RUNNER_TEMP}/genie-cli"
mkdir -p "$dir" && cd "$dir"
curl -fsSL -o "$pkg" "$base/$pkg"
curl -fsSL -o "$pkg.sha256" "$base/$pkg.sha256"
expected="$(awk '{print $1}' "$pkg.sha256")"
actual="$( (sha256sum "$pkg" 2>/dev/null || shasum -a 256 "$pkg") | awk '{print $1}')"
if [[ "$expected" != "$actual" ]]; then echo "::error::Checksum mismatch for $pkg"; exit 1; fi
tar xzf "$pkg"
echo "$dir/bin" >> "$GITHUB_PATH"
echo "GENIE_BIN=$dir/bin/genie" >> "$GITHUB_ENV"

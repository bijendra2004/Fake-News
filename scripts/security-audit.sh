#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

echo "Running npm audit..."
npm audit --audit-level=high

echo "Running pip-audit..."
if command -v pip-audit >/dev/null 2>&1; then
  pip-audit
elif /usr/local/bin/python3 -m pip_audit >/dev/null 2>&1; then
  /usr/local/bin/python3 -m pip_audit
else
  echo "pip-audit is not installed. Install it with: python3 -m pip install pip-audit" >&2
fi

#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
elif [[ -x "$REPO_ROOT/.venv/Scripts/python.exe" ]]; then
  PYTHON_BIN="$REPO_ROOT/.venv/Scripts/python.exe"
else
  PYTHON_BIN="python3"
fi

exec "$PYTHON_BIN" "$ROOT_DIR/bin/env_check.py" "$@"

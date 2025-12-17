#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python
fi

cd "$ROOT/transcribe-suite"
"$PYTHON" -m tools.stage_cleanup --repo-root "$ROOT" "$@"

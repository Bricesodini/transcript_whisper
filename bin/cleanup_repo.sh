#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PKG="$ROOT/transcribe-suite"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python
fi
TS="$(date +%Y%m%d_%H%M%S)"
LOGDIR="$ROOT/logs"
mkdir -p "$LOGDIR"
LOGFILE="$LOGDIR/cleanup_${TS}.log"

cd "$ROOT/transcribe-suite"
"$PYTHON" -m tools.cleanup_audit \
  --repo-root "$PKG" \
  --out-dir "$LOGDIR" \
  --log-file "$LOGFILE" \
  "$@"

echo "[cleanup] Audit done (reports/logs in \"$LOGDIR\", log: \"$LOGFILE\")"

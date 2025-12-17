#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python
fi

TS=$(date +%Y%m%d_%H%M%S)
LOGDIR="$ROOT/logs"
mkdir -p "$LOGDIR"
QA_LOG="$LOGDIR/qa_check_${TS}.log"
echo "[QA] Logs -> $QA_LOG"

echo "[QA] Tests unitaires (Transcribe Suite)"
cd "$ROOT"
"$PYTHON" -m pytest transcribe-suite/tests/unit >>"$QA_LOG" 2>&1

echo "[QA] Tests Control Room"
"$PYTHON" -m pytest tests/control_room >>"$QA_LOG" 2>&1

echo "[QA] Audit dépôt (dry-run)"
cd "$ROOT/transcribe-suite"
"$PYTHON" -m tools.cleanup_audit --repo-root "$ROOT/transcribe-suite" --out-dir logs --fail-on-legacy >>"$QA_LOG" 2>&1

if [[ -n "${DATA_PIPELINE_ROOT:-}" ]]; then
  echo "[QA] NAS audit (dry-run)"
  "$PYTHON" -m tools.nas_audit --root "$DATA_PIPELINE_ROOT" --out-dir logs >>"$QA_LOG" 2>&1
else
  echo "[QA] NAS audit ignoré (DATA_PIPELINE_ROOT non défini)" | tee -a "$QA_LOG"
fi

PORT=$(python - <<'PY'
import random
print(random.randint(8200, 8900))
PY
)
echo "[QA] Smoke Control Room (port $PORT)"
"$ROOT/bin/control_room_smoke.bat" --port "$PORT" >>"$QA_LOG" 2>&1

echo "[QA] OK"

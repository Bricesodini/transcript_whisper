#!/usr/bin/env bash
# Prépare les variables CPU pour les étapes post-ASR (align, diar, exports).
set -euo pipefail

if [[ -z "${POST_THREADS:-}" ]]; then
  POST_THREADS="$(python3 - <<'PY'
import os
print(max(6, (os.cpu_count() or 8) - 1))
PY
)"
fi

export POST_THREADS

_DEFAULT_THREADS="${POST_THREADS}"

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-${_DEFAULT_THREADS}}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-${_DEFAULT_THREADS}}"
export VECLIB_MAXIMUM_THREADS="${VECLIB_MAXIMUM_THREADS:-${_DEFAULT_THREADS}}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-${_DEFAULT_THREADS}}"

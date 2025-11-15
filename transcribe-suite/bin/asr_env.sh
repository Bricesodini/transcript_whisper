#!/usr/bin/env bash
# Calcule et exporte les variables CPU pour Faster-Whisper/CTRANSLATE2.
set -euo pipefail

if [[ -z "${ASR_THREADS:-}" ]]; then
  ASR_THREADS="$(python3 - <<'PY'
import os
print(max(8, (os.cpu_count() or 8) - 2))
PY
)"
fi

export ASR_THREADS

_DEFAULT_THREADS="${ASR_THREADS}"

export OMP_NUM_THREADS="${OMP_NUM_THREADS:-${_DEFAULT_THREADS}}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-${_DEFAULT_THREADS}}"
export VECLIB_MAXIMUM_THREADS="${VECLIB_MAXIMUM_THREADS:-${_DEFAULT_THREADS}}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-${_DEFAULT_THREADS}}"
export CTRANSLATE2_NUM_THREADS="${CTRANSLATE2_NUM_THREADS:-${_DEFAULT_THREADS}}"

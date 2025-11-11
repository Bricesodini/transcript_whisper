#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_PATH="$ROOT_DIR/config/config.yaml"
PYTHON=${PYTHON:-python3}

export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"

if [[ -f "$ROOT_DIR/.env.local" ]]; then
  set -a
  source "$ROOT_DIR/.env.local"
  set +a
fi

usage() {
  cat <<USAGE
Usage: run.sh --input /path/to/file [--lang auto] [--profile default] [--export txt,md]

Options mirror the Python CLI (see README for details).
USAGE
}

if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

$PYTHON "$ROOT_DIR/src/pipeline.py" --config "$CONFIG_PATH" "$@"

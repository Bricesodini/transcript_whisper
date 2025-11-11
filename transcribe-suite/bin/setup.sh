#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PATH="${VENV_PATH:-$ROOT_DIR/.venv}"
PYTHON=${PYTHON:-python3}
REQ_FILE="$ROOT_DIR/requirements.txt"

if [[ ! -f "$REQ_FILE" ]]; then
  echo "requirements.txt introuvable à $REQ_FILE" >&2
  exit 1
fi

if [[ ! -d "$VENV_PATH" ]]; then
  echo "Création de l'environnement virtuel dans $VENV_PATH"
  $PYTHON -m venv "$VENV_PATH"
fi

source "$VENV_PATH/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$REQ_FILE"

echo "Installation terminée. Activez l'environnement avec: source $VENV_PATH/bin/activate"

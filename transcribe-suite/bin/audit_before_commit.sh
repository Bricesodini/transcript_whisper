#!/usr/bin/env bash
set -euo pipefail

# ===== Config =====
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_DIR="$PROJECT_ROOT/logs"
REPORT="$REPORT_DIR/precommit_audit_$(date +%Y%m%d_%H%M%S).txt"
mkdir -p "$REPORT_DIR"

cd "$PROJECT_ROOT"

mask_secrets() {
  sed -E 's/(hf_[A-Za-z0-9_-]{4})[A-Za-z0-9_-]{16,}/\1***REDACTED***/g' \
  | sed -E 's/(sk-[A-Za-z0-9_-]{4})[A-Za-z0-9_-]{16,}/\1***REDACTED***/g'
}

echo "== PRE-COMMIT AUDIT ==" | tee "$REPORT"
echo "Date: $(date)" | tee -a "$REPORT"
echo "Root: $PROJECT_ROOT" | tee -a "$REPORT"
echo | tee -a "$REPORT"

# 0) Vérification env de base
echo "## 0) Env check" | tee -a "$REPORT"
if [[ -f bin/env_check.sh ]]; then
  if ! bash bin/env_check.sh 2>&1 | tee -a "$REPORT"; then
    true
  fi
else
  echo "bin/env_check.sh manquant (ok si non requis)." | tee -a "$REPORT"
fi
echo | tee -a "$REPORT"

# 1) Secrets & tokens (dans l’arbre ET dans l’index Git)
echo "## 1) Scan secrets" | tee -a "$REPORT"
PATTERN='(hf_[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}|api[_-]?key|secret|token|PYANNOTE_TOKEN|OPENAI_API_KEY)'
echo "-> grep dans le filesystem (exclut .git/.venv/work/exports/models/cache)" | tee -a "$REPORT"
if ! grep -RInE "$PATTERN" . \
  --exclude-dir=".git" --exclude-dir=".venv" \
  --exclude-dir="work" --exclude-dir="exports" \
  --exclude-dir="models" --exclude-dir=".cache" \
  --exclude="*.lock" --exclude="*.log" 2>&1 | mask_secrets | tee -a "$REPORT"; then
  true
fi

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "-> git grep (index)" | tee -a "$REPORT"
  if ! git grep -InE "$PATTERN" 2>&1 | mask_secrets | tee -a "$REPORT"; then
    true
  fi
else
  echo "git grep ignoré (pas de repo Git détecté)" | tee -a "$REPORT"
fi
echo | tee -a "$REPORT"

# 2) Gitignore & fichiers volumineux non ignorés
echo "## 2) Ignorés & lourds" | tee -a "$REPORT"
echo "-> Vérif patterns .gitignore pour chemins critiques" | tee -a "$REPORT"
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  for p in "work/" "exports/" "models/" ".venv/" ".cache/"; do
    if git check-ignore -q "$p"; then
      echo "OK ignore: $p" | tee -a "$REPORT"
    else
      echo "⚠️  non ignoré: $p (ajoute-le dans .gitignore)" | tee -a "$REPORT"
    fi
  done
else
  echo "git check-ignore ignoré (pas de repo Git détecté)" | tee -a "$REPORT"
fi

echo "-> Fichiers >50MB non ignorés" | tee -a "$REPORT"
if ! find . -type f -size +50M \
  -not -path "./.git/*" -not -path "./work/*" -not -path "./exports/*" \
  -not -path "./models/*" -not -path "./.venv/*" -print | tee -a "$REPORT"; then
  true
fi
echo | tee -a "$REPORT"

# 3) Nettoyage artefacts & orphelins
echo "## 3) Orphelins & artefacts" | tee -a "$REPORT"
echo "-> __pycache__ / .DS_Store / *.pyc" | tee -a "$REPORT"
if ! find . -type d -name "__pycache__" \
  -not -path "./.venv/*" -not -path "./work/*" -not -path "./exports/*" \
  -print | tee -a "$REPORT"; then
  true
fi
if ! find . -type f -name ".DS_Store" -print | tee -a "$REPORT"; then
  true
fi
if ! find . -type f -name "*.pyc" \
  -not -path "./.venv/*" -not -path "./work/*" -not -path "./exports/*" \
  -print | tee -a "$REPORT"; then
  true
fi

echo "-> Liens symboliques cassés" | tee -a "$REPORT"
if ! find . -type l ! -exec test -e {} \; -print | tee -a "$REPORT"; then
  true
fi
echo | tee -a "$REPORT"

# 4) Qualité code (syntaxe, import morts rapide)
echo "## 4) Qualité code" | tee -a "$REPORT"
echo "-> Compile Python (erreurs de syntaxe)" | tee -a "$REPORT"
if ! python -m compileall -q src > >(tee -a "$REPORT") 2>&1; then
  echo "⚠️  compileall a signalé des erreurs" | tee -a "$REPORT"
fi

if command -v ruff >/dev/null 2>&1; then
  echo "-> Ruff (lint rapide)" | tee -a "$REPORT"
  if ! ruff check src 2>&1 | tee -a "$REPORT"; then
    true
  fi
else
  echo "ruff non installé (ok)." | tee -a "$REPORT"
fi
echo | tee -a "$REPORT"

# 5) Docs: README & STABLE_BASE
echo "## 5) Docs" | tee -a "$REPORT"
if [[ -f README.md ]]; then
  echo "README.md présent" | tee -a "$REPORT"
else
  echo "⚠️  README.md manquant" | tee -a "$REPORT"
fi

if [[ -f docs/STABLE_BASE.md ]]; then
  echo "docs/STABLE_BASE.md présent" | tee -a "$REPORT"
else
  echo "⚠️  docs/STABLE_BASE.md manquant" | tee -a "$REPORT"
fi

echo "-> Titres attendus dans README" | tee -a "$REPORT"
if [[ -f README.md ]]; then
  if ! grep -nE '^(# |## )(Installation|Usage|CLI|Artefacts|FAQ|Licence|Changelog)' README.md 2>&1 | tee -a "$REPORT"; then
    true
  fi
else
  echo "README.md introuvable pour la vérification des titres." | tee -a "$REPORT"
fi
echo | tee -a "$REPORT"

# 6) Dry-run pipeline (zéro ML, zéro token)
echo "## 6) Dry-run pipeline" | tee -a "$REPORT"
if [[ -x bin/run.sh ]]; then
  if ! NO_TK=1 CT2_USE_MPS=1 bin/run.sh dry-run --input "/tmp/placeholder.wav" 2>&1 | tee -a "$REPORT"; then
    true
  fi
else
  echo "bin/run.sh introuvable/exécutable manquant" | tee -a "$REPORT"
fi
echo | tee -a "$REPORT"

# 7) Diff dépendances vs lock
echo "## 7) Dépendances vs lock" | tee -a "$REPORT"
if [[ -f requirements.lock ]]; then
  echo "Paquets installés (top 30) :" | tee -a "$REPORT"
  if ! pip freeze | head -n 30 | tee -a "$REPORT"; then
    true
  fi
  echo | tee -a "$REPORT"
else
  echo "⚠️  requirements.lock manquant" | tee -a "$REPORT"
fi

echo "== FIN AUDIT ==" | tee -a "$REPORT"
echo "Rapport: $REPORT" | tee -a "$REPORT"

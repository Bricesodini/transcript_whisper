#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )/.." && pwd)"
PYTHON_BIN=${PYTHON:-python3}
REQUIREMENTS_LOCK="$ROOT_DIR/requirements.lock"
REQUIRED_PYTHON_MAJOR=3
REQUIRED_PYTHON_MINOR=11
REQUIRED_PYTHON_LABEL="3.11.x"
FFMPEG_MIN_MAJOR=6
FFMPEG_MAX_MAJOR=8

REQUIRED_PKGS=(
  "faster-whisper=1.1.1"
  "whisperx=3.7.4"
  "pyannote.audio=3.4.0"
  "torch=2.8.0"
  "torchaudio=2.8.0"
  "onnxruntime=1.23.2"
  "ctranslate2=4.6.1"
)

fail() {
  echo "[env_check] $1" >&2
  exit 1
}

if [[ ! -f "$REQUIREMENTS_LOCK" ]]; then
  fail "requirements.lock introuvable ($REQUIREMENTS_LOCK)"
fi

PY_VERSION=$($PYTHON_BIN -c 'import platform; print(platform.python_version())')
IFS=. read -r PY_MAJOR PY_MINOR _ <<<"$PY_VERSION"
if [[ "$PY_MAJOR" != "$REQUIRED_PYTHON_MAJOR" || "$PY_MINOR" != "$REQUIRED_PYTHON_MINOR" ]]; then
  fail "Python $REQUIRED_PYTHON_LABEL requis, trouvé $PY_VERSION (export PYTHON=...)."
fi

pkg_version() {
  local pkg="$1"
  $PYTHON_BIN - "$pkg" <<'PY'
import importlib, sys
name = sys.argv[1]
module_name = name.replace('-', '_')
version = None
try:
    module = importlib.import_module(module_name)
    version = getattr(module, "__version__", None)
except Exception:
    version = None
if not version:
    try:
        import pkg_resources
        version = pkg_resources.get_distribution(name).version
    except Exception:
        version = None
print(version or "missing")
PY
}

for entry in "${REQUIRED_PKGS[@]}"; do
  pkg="${entry%%=*}"
  required="${entry##*=}"
  installed=$(pkg_version "$pkg")
  if [[ "$installed" != "$required" ]]; then
    fail "Package $pkg=$required requis, trouvé $installed"
  fi
done

check_ff_bin() {
  local bin="$1"
  local label="$2"
  if ! command -v "$bin" >/dev/null 2>&1; then
    fail "$label introuvable (brew install ffmpeg)"
  fi
  local version=$($bin -version | head -n1 | awk '{print $3}')
  local major=${version%%.*}
  if [[ -z "$major" || ! "$major" =~ ^[0-9]+$ ]]; then
    fail "Impossible de déterminer la version $label (sortie: $version)"
  fi
  if (( major < FFMPEG_MIN_MAJOR || major > FFMPEG_MAX_MAJOR )); then
    fail "$label version $version (major=$major) hors plage [$FFMPEG_MIN_MAJOR-$FFMPEG_MAX_MAJOR]"
  fi
  echo "$version"
}

FFMPEG_VERSION=$(check_ff_bin ffmpeg "ffmpeg")
FFPROBE_VERSION=$(check_ff_bin ffprobe "ffprobe")

echo "Environnement conforme aux exigences verrouillées (Python $PY_VERSION, ffmpeg $FFMPEG_VERSION, ffprobe $FFPROBE_VERSION)."

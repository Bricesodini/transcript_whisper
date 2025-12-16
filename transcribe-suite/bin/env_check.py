#!/usr/bin/env python3
"""Cross-platform environment checker for Transcribe Suite."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict


ROOT_DIR = Path(__file__).resolve().parents[1]
REQUIREMENTS_LOCK = ROOT_DIR / "requirements.lock"
SUPPORTED_PYTHON = {(3, 11), (3, 12)}
SUPPORTED_LABEL = "3.11.x ou 3.12.x"
FFMPEG_MIN_MAJOR = 6
FFMPEG_MAX_MAJOR = 8

COMMON_PKGS: Dict[str, str] = {
    "faster-whisper": "1.2.1",
    "whisperx": "3.4.0",
    "pyannote.audio": "3.4.0",
    "ctranslate2": "4.4.0",
}

if os.name == "nt":
    PLATFORM_PKGS: Dict[str, str] = {
        "torch": "2.6.0+cu124",
        "torchaudio": "2.6.0+cu124",
        "onnxruntime-gpu": "1.23.2",
    }
else:
    PLATFORM_PKGS = {
        "torch": "2.8.0",
        "torchaudio": "2.8.0",
        "onnxruntime": "1.23.2",
    }

MODULE_ALIASES = {
    "faster-whisper": "faster_whisper",
    "onnxruntime-gpu": "onnxruntime",
}


class EnvCheckError(RuntimeError):
    """Custom error for clear messaging."""


def fail(message: str) -> None:
    raise EnvCheckError(message)


def ensure_requirements_lock() -> None:
    if not REQUIREMENTS_LOCK.exists():
        fail(f"requirements.lock introuvable ({REQUIREMENTS_LOCK})")


def ensure_python_version() -> str:
    version_info = sys.version_info
    py_tuple = (version_info.major, version_info.minor)
    if py_tuple not in SUPPORTED_PYTHON:
        fail(
            f"Python {SUPPORTED_LABEL} requis, trouvé "
            f"{version_info.major}.{version_info.minor}.{version_info.micro}"
        )
    return f"{version_info.major}.{version_info.minor}.{version_info.micro}"


def pkg_version(package: str) -> str | None:
    module_name = MODULE_ALIASES.get(package, package.replace("-", "_"))
    try:
        from importlib import metadata

        return metadata.version(package)
    except Exception:
        pass

    try:
        import importlib

        module = importlib.import_module(module_name)
        return getattr(module, "__version__", None)
    except Exception:
        return None


def ensure_packages() -> None:
    requirements = {**COMMON_PKGS, **PLATFORM_PKGS}
    for pkg, expected in requirements.items():
        installed = pkg_version(pkg)
        if installed != expected:
            fail(f"Package {pkg}={expected} requis, trouvé {installed or 'missing'}")


def check_ff_bin(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        fail(f"{binary} introuvable (installer ffmpeg/ffprobe dans le PATH)")

    try:
        result = subprocess.run(
            [path, "-version"], check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as exc:
        fail(f"Echec {binary} -version ({exc})")

    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    match = re.search(r"\b(\d+)\.", first_line)
    if match:
        major = int(match.group(1))
        if not (FFMPEG_MIN_MAJOR <= major <= FFMPEG_MAX_MAJOR):
            fail(
                f"{binary} version {first_line.split()[2]} "
                f"(major={major}) hors plage [{FFMPEG_MIN_MAJOR}-{FFMPEG_MAX_MAJOR}]"
            )
    else:
        print(
            f"[env_check] Version {binary} non standard ({first_line}), "
            "contrôle de plage ignoré.",
            file=sys.stderr,
        )
    return first_line.split()[2] if first_line else "unknown"


def check_mps() -> None:
    try:
        import torch

        available = torch.backends.mps.is_available()
        print("torch.backends.mps.is_available() =", available)
    except Exception as exc:
        print(f"torch.backends.mps.is_available() indisponible: {exc}")


def main() -> None:
    ensure_requirements_lock()
    py_version = ensure_python_version()
    ensure_packages()
    ffmpeg_version = check_ff_bin("ffmpeg")
    ffprobe_version = check_ff_bin("ffprobe")
    print(">>> Checking Metal (optional, not required)...")
    check_mps()
    print(
        f"Environnement conforme aux exigences verrouillées "
        f"(Python {py_version}, ffmpeg {ffmpeg_version}, ffprobe {ffprobe_version})."
    )


if __name__ == "__main__":
    try:
        main()
    except EnvCheckError as error:
        print(f"[env_check] {error}", file=sys.stderr)
        sys.exit(1)

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
import hashlib

THREAD_VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS")


class PipelineError(RuntimeError):
    """Custom exception for pipeline failures."""


def compute_post_threads() -> int:
    cores = os.cpu_count() or 8
    return max(6, cores - 1)


def apply_thread_env(label: str, threads: int) -> None:
    target = max(1, int(threads))
    if label:
        os.environ[label] = str(target)
    for var in THREAD_VARS:
        os.environ[var] = str(target)


def configure_torch_threads(num_threads: int, interop_threads: int = 2) -> None:
    try:
        import torch  # type: ignore
    except ImportError:
        return
    try:
        torch.set_num_threads(max(1, int(num_threads)))
    except Exception:
        pass
    try:
        torch.set_num_interop_threads(max(1, int(interop_threads)))
    except Exception:
        pass


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result: Dict[str, Any] = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def select_profile(config: Dict[str, Any], profile_name: str) -> Dict[str, Any]:
    profiles = config.get("profiles", {})
    profile = profiles.get(profile_name, {})
    if not profile:
        return config
    merged = dict(config)
    for key, value in profile.items():
        if key == "description":
            continue
        if key in merged and isinstance(merged[key], dict):
            merged[key] = merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def prepare_paths(root: Path, cfg: Dict[str, Any]) -> Dict[str, Path]:
    paths_cfg = cfg.get("paths", {})
    defaults = {
        "inputs_dir": root / "inputs",
        "work_dir": root / "work",
        "exports_dir": root / "exports",
        "logs_dir": root / "logs",
        "cache_dir": root / "cache",
    }
    resolved: Dict[str, Path] = {}
    for key, default in defaults.items():
        rel = paths_cfg.get(key)
        target = (root / rel).resolve() if rel else default.resolve()
        target.mkdir(parents=True, exist_ok=True)
        resolved[key] = target
    return resolved


def setup_logger(log_dir: Path, run_name: str, log_level: str = "INFO") -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_name}.log"
    logger = logging.getLogger(f"transcribe-suite.{run_name}")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    level_name = str(log_level or "INFO").upper()
    console_level = getattr(logging, level_name, logging.INFO)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(console_level)
    logger.addHandler(sh)
    return logger


def run_cmd(cmd: List[str], logger: logging.Logger, cwd: Optional[Path] = None) -> None:
    logger.debug("RUN: %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        logger.error(result.stdout)
        raise PipelineError(f"Command failed: {' '.join(cmd)}")
    if result.stdout:
        logger.debug(result.stdout.strip())


def copy_to_clipboard(text: str, logger: logging.Logger) -> None:
    if shutil.which("pbcopy") is None:
        logger.debug("pbcopy not available; skipping clipboard copy")
        return
    try:
        proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        proc.communicate(text.encode("utf-8"))
    except Exception as exc:  # pragma: no cover
        logger.warning("Clipboard copy failed: %s", exc)


def write_json(path: Path, payload: Any, indent: int = 2) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=indent)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


@contextmanager
def stage_timer(logger: logging.Logger, label: str):
    logger.info("▶ %s", label)
    try:
        yield
        logger.info("✔ %s", label)
    except Exception:
        logger.exception("Stage failed: %s", label)
        raise


def detect_language(text: str) -> Optional[str]:
    try:
        from langdetect import detect
    except ImportError:  # pragma: no cover
        return None
    try:
        return detect(text)
    except Exception:
        return None


def sanitize_whisper_text(text: Any) -> str:
    if text is None:
        return ""

    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")

    text = str(text)
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00A0", " ")
    text = "".join(ch for ch in text if ch == "\n" or ord(ch) >= 32)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def normalize_media_path(raw: Optional[Any]) -> Optional[str]:
    """Clean paths coming from CLI/Shortcuts (handles stray quotes and '\ ' sequences)."""
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        raw = raw[0] if raw else None
        if raw is None:
            return None
    if isinstance(raw, Path):
        raw = str(raw)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    for sep in ("\x00", "\r", "\n"):
        cleaned = cleaned.replace(sep, "")
    if cleaned.startswith(("'", '"')) and cleaned.endswith(("'", '"')):
        cleaned = cleaned[1:-1]
    if cleaned.startswith("file://"):
        from urllib.parse import unquote

        cleaned = unquote(cleaned[7:])
    cleaned = cleaned.replace("\\ ", " ")
    return cleaned or None


def stable_id(source_path: str, ts_start: float, ts_end: float, speaker: Optional[str] = None) -> str:
    """Generate a deterministic identifier for artifacts."""
    payload = {
        "src": source_path,
        "t0": round(float(ts_start or 0.0), 3),
        "t1": round(float(ts_end or ts_start or 0.0), 3),
        "spk": speaker or "",
    }
    digest_input = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha1(digest_input).hexdigest()[:12]

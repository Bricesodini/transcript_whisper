"""Helpers for NAS data-pipeline overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

PIPELINE_ROOT_ENV = "DATA_PIPELINE_ROOT"
PIPELINE_RAG_SUBDIR = "03_output_RAG"


def _pipeline_root_from_env() -> Optional[Path]:
    raw = os.environ.get(PIPELINE_ROOT_ENV)
    if not raw:
        return None
    return Path(raw).expanduser()


def resolve_rag_output_override() -> Optional[Path]:
    """Return the pipeline override for RAG outputs if configured."""
    root = _pipeline_root_from_env()
    if not root:
        return None
    return (root / PIPELINE_RAG_SUBDIR).resolve()

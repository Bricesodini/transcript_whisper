"""Helpers for resolving RAG output directories."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from utils import PipelineError

from . import PROJECT_ROOT, RAG_SCHEMA_VERSION
from .doc_id import resolve_doc_id
from .resolver import InputResolver


def resolve_rag_directory(
    input_path: Path,
    *,
    version_tag: Optional[str],
    doc_id_override: Optional[str],
    config_bundle,
    logger,
) -> Path:
    config = dict(config_bundle.effective)
    output_root = _resolve_output_root(config)
    input_path = input_path.expanduser().resolve()

    if input_path.is_file():
        input_path = input_path.parent
    if (input_path / "document.json").exists():
        return input_path

    # Allow pointing directly to parent containing RAG-* directories.
    candidate = _latest_version_dir(input_path)
    if candidate:
        return candidate

    resolver = InputResolver(PROJECT_ROOT, logger)
    resolved = resolver.resolve(input_path)
    doc_id = resolve_doc_id(
        resolved.doc_title,
        str(resolved.media_path or resolved.work_dir),
        config.get("doc_id"),
        doc_id_override,
    )
    prefix = config.get("export_dir_prefix") or "RAG-"
    parent = output_root / f"{prefix}{doc_id}"
    if not parent.exists():
        raise PipelineError(f"Dossier RAG introuvable pour {doc_id}: {parent}")
    if version_tag:
        target = parent / version_tag
        if not (target / "document.json").exists():
            raise PipelineError(f"Version RAG introuvable: {target}")
        return target
    default = parent / (config.get("schema_version") or RAG_SCHEMA_VERSION)
    if (default / "document.json").exists():
        return default
    latest = _latest_version_dir(parent)
    if latest:
        return latest
    raise PipelineError(f"Aucune version RAG trouvée dans {parent}")


def _resolve_output_root(config: dict) -> Path:
    output_dir = Path(config.get("output_dir") or "RAG")
    if not output_dir.is_absolute():
        output_dir = (PROJECT_ROOT / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _latest_version_dir(parent: Path) -> Optional[Path]:
    if not parent.exists() or not parent.is_dir():
        return None
    candidates = [
        child for child in parent.iterdir() if child.is_dir() and (child / "document.json").exists()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]

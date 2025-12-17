from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .settings import Settings


@dataclass
class StorageDir:
    label: str
    path: str
    exists: bool
    size_bytes: int
    items: int
    oldest: Optional[str]
    newest: Optional[str]


@dataclass
class HeavyDoc:
    doc_id: str
    size_bytes: int
    location: str


def _dir_size(path: Path) -> Tuple[int, int, Optional[float], Optional[float]]:
    total = 0
    count = 0
    oldest = None
    newest = None
    if not path.exists():
        return 0, 0, None, None
    for file_path in path.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            stat = file_path.stat()
        except OSError:
            continue
        total += stat.st_size
        count += 1
        ts = stat.st_mtime
        if oldest is None or ts < oldest:
            oldest = ts
        if newest is None or ts > newest:
            newest = ts
    return total, count, oldest, newest


def _format_timestamp(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def collect_storage_snapshot(settings: Settings, top_n: int = 5) -> Dict[str, object]:
    root = settings.data_pipeline_root
    dirs_summary: List[StorageDir] = []
    default_dirs = [
        ("01_input", "Inputs bruts"),
        ("02_output_source", "Sources ASR"),
        ("03_output_RAG", "Exports RAG"),
        ("04_archive", "Archives"),
    ]
    for name, label in default_dirs:
        folder = root / name
        size_bytes, items, oldest, newest = _dir_size(folder)
        dirs_summary.append(
            StorageDir(
                label=label,
                path=str(folder),
                exists=folder.exists(),
                size_bytes=size_bytes,
                items=items,
                oldest=_format_timestamp(oldest),
                newest=_format_timestamp(newest),
            )
        )

    asr_docs = _list_dirs(settings.data_pipeline_root / "02_output_source" / "asr")
    rag_docs = _list_rag_dirs(settings.data_pipeline_root / "03_output_RAG")
    heavy_docs = [
        HeavyDoc(doc_id=name, size_bytes=size, location="02_output_source/asr")
        for name, size in sorted(asr_docs.items(), key=lambda item: item[1], reverse=True)[:top_n]
    ]
    missing_rag = sorted([doc for doc in asr_docs.keys() if doc not in rag_docs])
    missing_source = sorted([doc for doc in rag_docs.keys() if doc not in asr_docs])

    return {
        "root": str(root),
        "directories": [entry.__dict__ for entry in dirs_summary],
        "heavy_docs": [entry.__dict__ for entry in heavy_docs],
        "orphans": {"missing_rag": missing_rag, "missing_source": missing_source},
    }


def _list_dirs(base: Path) -> Dict[str, int]:
    docs: Dict[str, int] = {}
    if not base.exists():
        return docs
    for child in base.iterdir():
        if not child.is_dir():
            continue
        size_bytes, _, _, _ = _dir_size(child)
        docs[child.name] = size_bytes
    return docs


def _list_rag_dirs(base: Path) -> Dict[str, int]:
    docs: Dict[str, int] = {}
    if not base.exists():
        return docs
    for child in base.iterdir():
        if not child.is_dir() or not child.name.startswith("RAG-"):
            continue
        doc_id = child.name[4:]
        size_bytes, _, _, _ = _dir_size(child)
        docs[doc_id] = size_bytes
    return docs

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .settings import Settings


DOC_LOCK_GLOBAL = "__global__"


@dataclass
class DocPaths:
    doc_id: str
    root: Path
    work_dir: Optional[Path]
    transcript_dir: Optional[Path]

    @property
    def suggested_glossary(self) -> Optional[Path]:
        if not self.work_dir:
            return None
        return self.work_dir / "rag.glossary.suggested.yaml"

    @property
    def validated_glossary(self) -> Optional[Path]:
        if not self.work_dir:
            return None
        return self.work_dir / "rag.glossary.yaml"

    @property
    def stamp_path(self) -> Optional[Path]:
        if not self.work_dir:
            return None
        return self.work_dir / ".lexicon_ok.json"


class ResolverError(ValueError):
    pass


def sanitize_doc_id(doc_id: str) -> str:
    value = doc_id.strip()
    if not value:
        raise ResolverError("doc_id vide.")
    forbidden = set("\\/:")
    if any(ch in forbidden for ch in value):
        raise ResolverError("doc_id invalide.")
    if ".." in value:
        raise ResolverError("doc_id invalide.")
    return value


def list_doc_roots(settings: Settings) -> Iterable[Path]:
    staging = settings.asr_staging_dir
    if not staging.exists():
        return []
    return sorted([p for p in staging.iterdir() if p.is_dir()])


def list_doc_paths(settings: Settings) -> Iterator[DocPaths]:
    for root in list_doc_roots(settings):
        yield doc_paths_from_root(root)


def resolve_doc(settings: Settings, doc_id: str) -> DocPaths:
    clean_id = sanitize_doc_id(doc_id)
    root = (settings.asr_staging_dir / clean_id).resolve()
    try:
        root.relative_to(settings.asr_staging_dir.resolve())
    except ValueError as exc:
        raise ResolverError("doc_id hors périmètre.") from exc
    if not root.exists():
        raise ResolverError("Document introuvable.")
    return doc_paths_from_root(root)


def doc_paths_from_root(root: Path) -> DocPaths:
    doc_id = root.name
    work_parent = root / "work"
    work_dir: Optional[Path] = None
    if work_parent.exists():
        preferred = work_parent / doc_id
        if preferred.exists():
            work_dir = preferred
        else:
            children = [p for p in work_parent.iterdir() if p.is_dir()]
            if len(children) == 1:
                work_dir = children[0]
    transcripts = [p for p in root.iterdir() if p.name.startswith("TRANSCRIPT")]
    transcript_dir = transcripts[0] if transcripts else None
    return DocPaths(doc_id=doc_id, root=root, work_dir=work_dir, transcript_dir=transcript_dir)

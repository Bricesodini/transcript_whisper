"""Input resolver for the RAG export pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from utils import PipelineError


@dataclass
class ResolvedPaths:
    doc_name: str
    doc_title: str
    work_dir: Path
    transcript_dir: Optional[Path]
    media_path: Optional[Path]
    polished_path: Path
    clean_txt_path: Optional[Path]
    clean_jsonl_path: Optional[Path]
    metrics_path: Optional[Path]
    chunks_path: Optional[Path]
    raw_segments_path: Optional[Path]
    warnings: List[str] = field(default_factory=list)
    notes: Dict[str, str] = field(default_factory=dict)


class InputResolver:
    """Resolve document-specific folders from the CLI input."""

    def __init__(self, project_root: Path, logger):
        self.project_root = project_root
        self.logger = logger
        self.work_root = self.project_root / "work"

    def resolve(self, entry: Path) -> ResolvedPaths:
        entry = entry.expanduser().resolve()
        doc_name: Optional[str] = None
        work_dir: Optional[Path] = None
        transcript_dir: Optional[Path] = None
        media_path: Optional[Path] = None

        if entry.is_file():
            media_path = entry
            doc_name = entry.stem
            work_dir = self._guess_work_dir(doc_name, strict=False) or self._search_nearby_work_dir(entry, doc_name)
        else:
            if (entry / "05_polished.json").exists():
                work_dir = entry
                doc_name = entry.name
            elif entry.name.startswith("TRANSCRIPT - "):
                doc_name = entry.name.split("TRANSCRIPT - ", 1)[-1].strip()
                transcript_dir = entry
                work_dir = self._guess_work_dir(doc_name, strict=False) or self._search_nearby_work_dir(entry, doc_name)
            else:
                # Maybe user pointed directly to doc name under work root
                potential = entry / "05_polished.json"
                if potential.exists():
                    work_dir = entry
                    doc_name = entry.name
                else:
                    # try treat as doc name within work
                    doc_name = entry.name
                    work_dir = self._guess_work_dir(doc_name, strict=False)
                    if work_dir is None:
                        raise PipelineError(f"Impossible d'inférer le dossier work pour: {entry}")

        if not work_dir or not work_dir.exists():
            raise PipelineError(f"Dossier work introuvable pour: {entry}")

        transcript_dir = transcript_dir or self._find_transcript_dir(doc_name, entry, media_path)
        polished_path = work_dir / "05_polished.json"
        if not polished_path.exists():
            raise PipelineError(f"05_polished.json introuvable dans {work_dir}")

        clean_txt_path = self._find_first_file(
            [
                transcript_dir,
                entry if entry.is_dir() else entry.parent,
                work_dir,
            ],
            pattern="*.clean.txt",
        )
        clean_jsonl_path = self._find_first_file(
            [transcript_dir, work_dir],
            pattern="*.clean.jsonl",
        )
        metrics_path = self._find_first_file(
            [transcript_dir, work_dir],
            pattern="*.metrics.json",
        )
        chunks_path = self._find_first_file(
            [transcript_dir, work_dir],
            pattern="*.chunks.jsonl",
        )
        if not chunks_path:
            chunks_path = self._find_first_file(
                [transcript_dir, work_dir],
                pattern="chunks.json",
            )
        raw_segments_path = self._find_first_file(
            [work_dir],
            pattern="02_merged_raw.json",
        )

        warnings: List[str] = []
        if not clean_txt_path:
            warnings.append("Texte .clean.txt introuvable: fallback sur 05_polished.json")
        if not metrics_path:
            warnings.append("metrics.json absent: confiances globales approximatives")
        if not chunks_path:
            warnings.append("chunks.jsonl absent: génération de nouveaux chunks")
        if not transcript_dir:
            warnings.append("Dossier TRANSCRIPT non trouvé pour ce document")

        doc_title = doc_name or work_dir.name
        notes: Dict[str, str] = {}
        if transcript_dir:
            notes["transcript_dir"] = str(transcript_dir)
        if clean_txt_path:
            notes["clean_txt"] = str(clean_txt_path)
        if clean_jsonl_path:
            notes["clean_jsonl"] = str(clean_jsonl_path)
        if metrics_path:
            notes["metrics"] = str(metrics_path)
        if chunks_path:
            notes["chunks"] = str(chunks_path)

        self.logger.info("Sources détectées: work=%s transcript=%s", work_dir, transcript_dir or "n/a")
        return ResolvedPaths(
            doc_name=doc_title,
            doc_title=doc_title,
            work_dir=work_dir,
            transcript_dir=transcript_dir,
            media_path=media_path,
            polished_path=polished_path,
            clean_txt_path=clean_txt_path,
            clean_jsonl_path=clean_jsonl_path,
            metrics_path=metrics_path,
            chunks_path=chunks_path,
            raw_segments_path=raw_segments_path,
            warnings=warnings,
            notes=notes,
        )

    def _guess_work_dir(self, doc_name: str, strict: bool = True) -> Optional[Path]:
        candidate = (self.work_root / doc_name).resolve()
        if candidate.exists():
            return candidate
        for child in self.work_root.glob("*"):
            if child.is_dir() and child.name.casefold() == doc_name.casefold():
                return child
        if strict:
            raise PipelineError(f"Dossier work introuvable pour '{doc_name}'")
        return None

    def _search_nearby_work_dir(self, entry: Path, doc_name: Optional[str]) -> Optional[Path]:
        doc_candidates = [name for name in [doc_name, entry.stem if entry.is_file() else entry.name] if name]
        visited: List[Path] = []
        current = entry
        for _ in range(4):
            if not current or current in visited:
                break
            visited.append(current)
            parents = [current, current / "work"]
            for base in parents:
                if not base or not base.exists():
                    continue
                for candidate_name in doc_candidates:
                    candidate = base / candidate_name
                    if candidate.exists() and (candidate / "05_polished.json").exists():
                        return candidate
            if current.parent == current:
                break
            current = current.parent
        return None

    def _find_transcript_dir(
        self,
        doc_name: Optional[str],
        entry: Path,
        media_path: Optional[Path],
    ) -> Optional[Path]:
        candidates = []
        if media_path:
            candidates.append(media_path.parent)
        if entry.is_dir():
            candidates.append(entry)
            candidates.append(entry.parent)
        candidates.append(self.project_root)
        for base in candidates:
            if not base or not base.exists():
                continue
            candidate = base / f"TRANSCRIPT - {doc_name}"
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _find_first_file(directories: List[Optional[Path]], pattern: str) -> Optional[Path]:
        for directory in directories:
            if not directory or not directory.exists():
                continue
            matches = sorted(directory.glob(pattern))
            for match in matches:
                if match.is_file():
                    return match
        return None

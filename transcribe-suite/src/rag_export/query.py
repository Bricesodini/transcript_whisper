"""Lightweight lexical query helper for RAG artefacts."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import PipelineError, setup_logger

from . import PROJECT_ROOT
from .configuration import ConfigBundle
from .pipeline import resolve_rag_output_override
from .targets import resolve_rag_directory


@dataclass
class RAGQueryOptions:
    input_path: Path
    query: str
    top_k: int = 5
    version_tag: Optional[str] = None
    doc_id_override: Optional[str] = None


class RAGQuery:
    """Execute SQLite FTS5 lookups to manually review chunk quality."""

    def __init__(self, options: RAGQueryOptions, config_bundle: ConfigBundle, *, log_level: str = "info"):
        self.options = options
        self.config_bundle = config_bundle
        self.config = dict(config_bundle.effective)
        self.output_root = self._resolve_output_root()
        self.log_dir = self._resolve_log_dir()
        run_name = self._build_run_name()
        self.logger = setup_logger(self.log_dir, run_name, log_level=log_level)

    def run(self) -> List[Dict[str, Any]]:
        query = (self.options.query or "").strip()
        if not query:
            raise PipelineError("Requete vide: preciser --query.")
        target_dir = resolve_rag_directory(
            self.options.input_path,
            version_tag=self.options.version_tag,
            doc_id_override=self.options.doc_id_override,
            config_bundle=self.config_bundle,
            logger=self.logger,
        )
        db_path = target_dir / "lexical.sqlite"
        if not db_path.exists():
            raise PipelineError(
                f"lexical.sqlite introuvable dans {target_dir}. Regenerer avec l'option SQLite activee."
            )
        chunks_map = self._load_chunks(target_dir / "chunks.jsonl")
        rows = self._query_sqlite(db_path, query=query, limit=max(1, int(self.options.top_k or 5)))
        enriched = []
        for row in rows:
            chunk = chunks_map.get(row["chunk_id"], {})
            enriched.append(
                {
                    "chunk_id": row["chunk_id"],
                    "doc_id": row["doc_id"],
                    "score": row["score"],
                    "start": chunk.get("start"),
                    "end": chunk.get("end"),
                    "text": chunk.get("text"),
                    "citation": chunk.get("citation"),
                    "confidence": chunk.get("confidence"),
                }
            )
        if not enriched:
            self.logger.warning("Aucun resultat pour '%s' dans %s.", query, db_path)
        else:
            self.logger.info("Resultats rag query (top=%d)", len(enriched))
            for idx, entry in enumerate(enriched, 1):
                citation = entry.get("citation") or {}
                citation_text = citation.get("text") or ""
                url = citation.get("url") or ""
                ts = ""
                if entry.get("start") is not None and entry.get("end") is not None:
                    ts = f"[{entry['start']:.2f}-{entry['end']:.2f}]"
                self.logger.info(
                    "%d. %s %s score=%.4f %s %s",
                    idx,
                    entry["chunk_id"],
                    ts,
                    entry["score"],
                    citation_text,
                    url,
                )
        return enriched

    def _query_sqlite(self, db_path: Path, *, query: str, limit: int) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT chunk_id, doc_id, bm25(chunks_fts) AS score "
                "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
                (query, limit),
            )
            results = [{"chunk_id": row[0], "doc_id": row[1], "score": float(row[2])} for row in cursor.fetchall()]
            return results
        finally:
            conn.close()

    def _load_chunks(self, jsonl_path: Path) -> Dict[str, Dict[str, Any]]:
        if not jsonl_path.exists():
            raise PipelineError(f"chunks.jsonl introuvable dans {jsonl_path.parent}.")
        mapping: Dict[str, Dict[str, Any]] = {}
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            chunk_id = record.get("chunk_id")
            if chunk_id:
                mapping[chunk_id] = record
        return mapping

    def _resolve_output_root(self) -> Path:
        override = resolve_rag_output_override()
        if override:
            output_dir = override
        else:
            output_dir = Path(self.config.get("output_dir") or "RAG")
        if not output_dir.is_absolute():
            output_dir = (PROJECT_ROOT / output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        return output_dir

    def _resolve_log_dir(self) -> Path:
        log_cfg = (self.config.get("logging") or {}).get("log_dir")
        if log_cfg:
            candidate = Path(log_cfg)
            if not candidate.is_absolute():
                candidate = (PROJECT_ROOT / candidate).resolve()
        else:
            candidate = (PROJECT_ROOT / "logs" / "rag").resolve()
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def _build_run_name(self) -> str:
        slug = self.options.input_path.name.replace(" ", "_")
        return f"rag_query_{slug}"

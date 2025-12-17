"""Validation for RAG exports."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils import PipelineError, setup_logger

from . import PROJECT_ROOT, RAG_SCHEMA_VERSION
from .configuration import ConfigBundle
from .generation import rel_path
from .targets import resolve_rag_directory


@dataclass
class RAGDoctorOptions:
    input_path: Path
    version_tag: Optional[str] = None
    doc_id_override: Optional[str] = None


class RAGDoctor:
    """Validate generated RAG artefacts."""

    def __init__(self, options: RAGDoctorOptions, config_bundle: ConfigBundle, *, log_level: str = "info"):
        self.options = options
        self.config_bundle = config_bundle
        self.config = dict(config_bundle.effective)
        self.schema_version = self.config.get("schema_version") or RAG_SCHEMA_VERSION
        self.output_root = self._resolve_output_root()
        self.log_dir = self._resolve_log_dir()
        run_name = self._build_run_name()
        self.logger = setup_logger(self.log_dir, run_name, log_level=log_level)
        health_cfg = self.config.get("health") or {}
        self.coverage_target = float(health_cfg.get("coverage_target_pct") or 0.0)
        self.sample_queries: List[str] = [q for q in health_cfg.get("sample_queries", []) if q]

    def run(self) -> bool:
        target_dir = resolve_rag_directory(
            self.options.input_path,
            version_tag=self.options.version_tag,
            doc_id_override=self.options.doc_id_override,
            config_bundle=self.config_bundle,
            logger=self.logger,
        )
        self.logger.info("Validation RAG: %s", target_dir)
        issues: List[str] = []
        warnings: List[str] = []

        required_files = [
            "document.json",
            "segments.jsonl",
            "chunks.jsonl",
            "quality.json",
            "README_RAG.md",
        ]
        for filename in required_files:
            if not (target_dir / filename).exists():
                issues.append(f"Fichier manquant: {filename}")

        manifest = self._load_json(target_dir / "document.json", "document.json", issues)
        segments = self._load_jsonl(target_dir / "segments.jsonl", "segments.jsonl", issues)
        chunks = self._load_jsonl(target_dir / "chunks.jsonl", "chunks.jsonl", issues)
        quality = self._load_json(target_dir / "quality.json", "quality.json", issues)

        if manifest and manifest.get("rag_schema_version") != RAG_SCHEMA_VERSION:
            warnings.append(
                f"Version de schéma inattendue: {manifest.get('rag_schema_version')} (attendu {RAG_SCHEMA_VERSION})"
            )

        if segments is not None and chunks is not None:
            self._validate_chunks(chunks, segments, issues)
            self._validate_coverage(chunks, segments, manifest, warnings)

        sqlite_expected = self._should_expect_sqlite(manifest)
        sqlite_path = target_dir / "lexical.sqlite"
        if sqlite_expected:
            self._check_sqlite(sqlite_path, issues, warnings)
        elif sqlite_path.exists():
            warnings.append("lexical.sqlite présent alors que l'indexation SQLite est désactivée.")

        if quality is None:
            warnings.append("quality.json illisible.")

        if issues:
            self.logger.error("Échec: %d problème(s) détecté(s).", len(issues))
            for issue in issues:
                self.logger.error(" - %s", issue)
            for warn in warnings:
                self.logger.warning(" - %s", warn)
            return False

        for warn in warnings:
            self.logger.warning(" - %s", warn)
        self.logger.info(
            "Doctor OK (segments=%d, chunks=%d, coverage cible=%.2f)",
            len(segments or []),
            len(chunks or []),
            self.coverage_target,
        )
        return True

    def _resolve_output_root(self) -> Path:
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
        return f"rag_doctor_{slug}"


    def _load_json(self, path: Path, label: str, issues: List[str]) -> Optional[Dict[str, Any]]:
        if not path.exists():
            issues.append(f"Fichier manquant: {label}")
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            issues.append(f"JSON invalide ({label}): {exc}")
            return None

    def _load_jsonl(self, path: Path, label: str, issues: List[str]) -> Optional[List[Dict[str, Any]]]:
        if not path.exists():
            issues.append(f"Fichier manquant: {label}")
            return None
        rows: List[Dict[str, Any]] = []
        try:
            for idx, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:  # pragma: no cover
                    issues.append(f"JSONL invalide ({label}, ligne {idx + 1}): {exc}")
                    return None
            return rows
        except Exception as exc:
            issues.append(f"Lecture impossible ({label}): {exc}")
            return None

    def _validate_chunks(self, chunks: List[Dict[str, Any]], segments: List[Dict[str, Any]], issues: List[str]) -> None:
        segment_ids = {segment.get("segment_id") for segment in segments}
        seen_chunks = set()
        for chunk in chunks:
            chunk_id = chunk.get("chunk_id")
            if chunk_id in seen_chunks:
                issues.append(f"chunk_id en double: {chunk_id}")
            seen_chunks.add(chunk_id)
            start = float(chunk.get("start", 0.0) or 0.0)
            end = float(chunk.get("end", start))
            if start > end:
                issues.append(f"Timestamps invalides pour chunk {chunk_id}: {start}>{end}")
            seg_refs = chunk.get("segment_ids") or []
            missing = [seg_id for seg_id in seg_refs if seg_id not in segment_ids]
            if missing:
                issues.append(f"Chunk {chunk_id} référence des segments inconnus: {missing}")

    def _validate_coverage(
        self,
        chunks: List[Dict[str, Any]],
        segments: List[Dict[str, Any]],
        manifest: Optional[Dict[str, Any]],
        warnings: List[str],
    ) -> None:
        if not chunks or not segments:
            return
        duration = None
        if manifest:
            duration = manifest.get("stats", {}).get("duration_s")
        if duration is None:
            duration = segments[-1].get("end")
        try:
            duration_val = float(duration)
        except (TypeError, ValueError):
            duration_val = None
        if not duration_val or duration_val <= 0:
            return
        start = float(chunks[0].get("start") or 0.0)
        end = float(chunks[-1].get("end") or start)
        coverage = max(0.0, min(1.0, (end - start) / duration_val)) if end >= start else 0.0
        if self.coverage_target and coverage < self.coverage_target:
            warnings.append(f"Couverture temporelle faible ({coverage:.2%} < {self.coverage_target:.2%}).")

    def _should_expect_sqlite(self, manifest: Optional[Dict[str, Any]]) -> bool:
        index_cfg = self.config.get("index") or {}
        if not index_cfg.get("enable_sqlite", True):
            return False
        if manifest and manifest.get("options", {}).get("no_sqlite"):
            return False
        return True

    def _check_sqlite(self, db_path: Path, issues: List[str], warnings: List[str]) -> None:
        if not db_path.exists():
            issues.append("lexical.sqlite manquant alors qu'il est attendu.")
            return
        conn = None
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_fts'")
            if not cursor.fetchone():
                issues.append("Index FTS5 manquant dans lexical.sqlite.")
                return
            queries = self.sample_queries or ["installation", "the"]
            matched = False
            for query in queries:
                cursor.execute("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT 1", (query,))
                if cursor.fetchone():
                    matched = True
                    break
            if not matched:
                warnings.append("FTS5: aucune correspondance pour les requêtes de test.")
        except sqlite3.DatabaseError as exc:
            issues.append(f"SQLite invalide: {exc}")
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

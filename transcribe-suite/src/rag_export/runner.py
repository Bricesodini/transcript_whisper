"""Core runner orchestration for the RAG export command."""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import hashlib

from utils import PipelineError, setup_logger, write_json

from . import PROJECT_ROOT, RAG_SCHEMA_VERSION
from .configuration import ConfigBundle
from .doc_id import resolve_doc_id
from .generation import (
    compute_file_sha256,
    rel_path,
    build_chunks_from_segments,
    build_llm_chunks,
    build_embedding_view,
    build_manifest,
    build_quality_report,
    build_sqlite_index,
    build_stats,
    load_existing_chunks,
    load_segments,
    write_config_effective,
    write_jsonl,
    write_readme,
)
from .resolver import InputResolver, ResolvedPaths
from .pipeline import resolve_rag_output_override
from .glossary import load_glossary_rules, merge_glossary_rules


@dataclass
class RAGExportOptions:
    input_path: Path
    base_url: Optional[str] = None
    lang: Optional[str] = None
    force: bool = False
    version_tag: Optional[str] = None
    no_sqlite: bool = False
    dry_run: bool = False
    doc_id_override: Optional[str] = None
    real_timestamps: bool = False


class RAGExportRunner:
    """Coordinates configuration, resolver, and artefact generation."""

    def __init__(self, options: RAGExportOptions, config: ConfigBundle, *, log_level: str = "info"):
        self.options = options
        self.config_bundle = config
        self.config = dict(config.effective)
        self.schema_version = self.config.get("schema_version") or RAG_SCHEMA_VERSION
        self.config_hash = self._hash_config()
        self.input_root = self._resolve_input_root(options.input_path)
        self.log_dir = self._resolve_log_dir()
        run_name = self._build_run_name()
        self.logger = setup_logger(self.log_dir, run_name, log_level=log_level)
        self.logger.debug("RAG config snapshot: %s", self.config_bundle.snapshot())
        self.logger.info("Schema RAG: %s", self.schema_version)
        self.output_root = self._resolve_output_root()

    def _resolve_input_root(self, raw_path: Path) -> Path:
        resolved = raw_path.expanduser().resolve()
        if not resolved.exists():
            raise PipelineError(f"Chemin d'entrée introuvable: {resolved}")
        return resolved

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

    def _build_run_name(self) -> str:
        ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        slug = self.input_root.name.replace(" ", "_")
        return f"rag_{slug}_{ts}"

    def run(self) -> None:
        resolver = InputResolver(PROJECT_ROOT, self.logger)
        resolved = resolver.resolve(self.input_root)
        doc_id = self._compute_doc_id(resolved)
        base_url = self.options.base_url or (self.config.get("citations") or {}).get("base_url")
        target_dir = self._compute_output_dir(doc_id)
        self.logger.info("Cible RAG: %s", target_dir)
        self._log_effective_overrides()
        target_exists = target_dir.exists()
        if self.options.dry_run:
            self.logger.info("Dry-run actif: aucune écriture sur disque.")
            if target_exists and not self.options.force:
                self.logger.warning("Le dossier existe déjà. Utiliser --force pour l'écraser lors de l'exécution réelle.")
            return
        previous_manifest = self._prepare_output_dir(target_dir)
        if previous_manifest:
            self._log_previous_stats(previous_manifest)
        deterministic_mode = not self.options.real_timestamps
        timestamps_policy = "real" if self.options.real_timestamps else "epoch"
        generated_at = self._current_timestamp() if self.options.real_timestamps else self._deterministic_timestamp()
        config_effective_path = target_dir / "config.effective.yaml"
        config_effective_sha = write_config_effective(config_effective_path, self.config_bundle.effective)
        segments, segments_info = load_segments(resolved)
        doc_lang = self.options.lang or segments_info.get("language") or "auto"
        chunk_cfg = self.config.get("chunks") or {}
        citation_cfg = self.config.get("citations") or {}
        quality_cfg = self.config.get("quality") or {}
        confidence_threshold = float(quality_cfg.get("threshold_warn") or 0.6)

        if resolved.chunks_path:
            chunks, chunk_info = load_existing_chunks(
                resolved.chunks_path,
                segments=segments,
                doc_id=doc_id,
                doc_title=resolved.doc_title,
                doc_lang=doc_lang,
                citation_cfg=citation_cfg,
                base_url=base_url,
                confidence_threshold=confidence_threshold,
            )
            self.logger.info("Chunks existants réutilisés: %s (n=%d)", resolved.chunks_path, len(chunks))
        else:
            chunks, chunk_info = build_chunks_from_segments(
                segments,
                doc_id=doc_id,
                doc_title=resolved.doc_title,
                doc_lang=doc_lang,
                target_tokens=int(chunk_cfg.get("target_tokens", 320)),
                overlap_tokens=int(chunk_cfg.get("overlap_tokens", 60)),
                citation_cfg=citation_cfg,
                base_url=base_url,
                confidence_threshold=confidence_threshold,
            )
            self.logger.info("Chunks générés: %d", len(chunks))

        if not chunks:
            raise PipelineError("Aucun chunk généré ou détecté.")

        stats = build_stats(
            segments,
            chunks,
            duration=segments_info["duration"],
            chunk_strategy=chunk_info,
            config_hash=self.config_hash,
        )
        quality = build_quality_report(segments, chunks, duration=segments_info["duration"], quality_cfg=quality_cfg)
        options_snapshot = {
            "base_url": base_url,
            "force": self.options.force,
            "version_tag": self.options.version_tag,
            "no_sqlite": self.options.no_sqlite,
            "lang_override": self.options.lang,
            "real_timestamps": self.options.real_timestamps,
        }
        clean_text_path = resolved.clean_txt_path or resolved.polished_path
        metrics_path = resolved.metrics_path or resolved.polished_path
        provenance = self._build_provenance(
            resolved=resolved,
            clean_text_path=clean_text_path,
            metrics_path=metrics_path,
            chunk_source=resolved.chunks_path,
            chunk_info=chunk_info,
            config_effective_path=config_effective_path,
            config_effective_sha=config_effective_sha,
        )
        manifest = build_manifest(
            doc_id=doc_id,
            doc_title=resolved.doc_title,
            doc_lang=doc_lang,
            generated_at=generated_at,
            resolved=resolved,
            config_snapshot=self.config_bundle.snapshot(),
            segments_info=segments_info,
            chunk_info=chunk_info,
            stats=stats,
            warnings=resolved.warnings,
            options=options_snapshot,
            provenance=provenance,
            config_effective_path=config_effective_path,
            config_effective_sha256=config_effective_sha,
            deterministic_mode=deterministic_mode,
            timestamps_policy=timestamps_policy,
        )

        write_json(target_dir / "document.json", manifest, sort_keys=True)
        write_jsonl(target_dir / "segments.jsonl", segments)
        write_jsonl(target_dir / "chunks.jsonl", chunks)
        text_norm_cfg = dict(self.config.get("text_normalization") or {})
        validated_rules = self._load_validated_glossary(resolved.work_dir)
        if validated_rules:
            merged_rules = merge_glossary_rules(text_norm_cfg.get("glossary") or [], validated_rules)
            text_norm_cfg["glossary"] = merged_rules
        else:
            text_norm_cfg.setdefault("glossary", text_norm_cfg.get("glossary") or [])
        embedding_view = build_embedding_view(
            chunks,
            doc_id=doc_id,
            doc_title=resolved.doc_title,
            doc_lang=doc_lang,
            text_norm_cfg=text_norm_cfg,
        )
        if embedding_view:
            embed_cfg = (text_norm_cfg.get("embedding_view") or {})
            embed_name = embed_cfg.get("output_filename") or "chunks_for_embedding.jsonl"
            write_jsonl(target_dir / embed_name, embedding_view)
            self.logger.info("Vue embeddings créée: %s", target_dir / embed_name)
        if chunk_cfg.get("llm_chunks_enabled"):
            llm_chunks = build_llm_chunks(chunks, doc_id=doc_id, doc_title=resolved.doc_title, doc_lang=doc_lang)
            write_jsonl(target_dir / "chunks_for_llm.jsonl", llm_chunks)
        write_json(target_dir / "quality.json", quality, sort_keys=True)
        write_readme(
            target_dir / "README_RAG.md",
            doc_id=doc_id,
            doc_title=resolved.doc_title,
            generated_at=generated_at,
            stats=stats,
            output_dir=target_dir,
        )

        index_cfg = self.config.get("index") or {}
        if index_cfg.get("enable_sqlite", True) and not self.options.no_sqlite:
            build_sqlite_index(chunks, target_dir / "lexical.sqlite")
            self.logger.info("Index lexical SQLite généré.")
        else:
            self.logger.info("Index lexical désactivé.")

        self.logger.info(
            "Export RAG terminé: segments=%d chunks=%d durée=%.2fs -> %s",
            stats["nb_segments"],
            stats["nb_chunks"],
            stats["duration_s"],
            target_dir,
        )

    def _compute_doc_id(self, resolved: ResolvedPaths) -> str:
        doc_cfg = self.config.get("doc_id") or {}
        source_key = str(resolved.media_path or resolved.work_dir)
        return resolve_doc_id(resolved.doc_title, source_key, doc_cfg, self.options.doc_id_override)

    def _compute_output_dir(self, doc_id: str) -> Path:
        prefix = self.config.get("export_dir_prefix") or "RAG-"
        parent = self.output_root / f"{prefix}{doc_id}"
        version_tag = self._normalized_version_tag() or self.schema_version
        return parent / version_tag

    def _normalized_version_tag(self) -> Optional[str]:
        if not self.options.version_tag:
            return None
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", self.options.version_tag.strip())
        return cleaned or None

    def _prepare_output_dir(self, target: Path) -> Optional[Dict[str, Any]]:
        if target.exists():
            if not self.options.force:
                raise PipelineError(f"Le dossier {target} existe déjà. Utiliser --force pour écraser.")
            previous_manifest = self._read_manifest(target / "document.json")
            shutil.rmtree(target)
        else:
            previous_manifest = None
        target.mkdir(parents=True, exist_ok=True)
        return previous_manifest

    @staticmethod
    def _read_manifest(path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _hash_config(self) -> str:
        payload = json.dumps(self.config_bundle.effective, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _current_timestamp(self) -> str:
        return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()

    def _deterministic_timestamp(self) -> str:
        return "1970-01-01T00:00:00Z"

    def _log_previous_stats(self, manifest: Dict[str, Any]) -> None:
        stats = manifest.get("stats") or {}
        self.logger.info(
            "Run précédent: schema=%s segments=%s chunks=%s",
            manifest.get("rag_schema_version"),
            stats.get("nb_segments"),
            stats.get("nb_chunks"),
        )

    def _build_provenance(
        self,
        *,
        resolved: ResolvedPaths,
        clean_text_path: Path,
        metrics_path: Path,
        chunk_source: Optional[Path],
        chunk_info: Dict[str, Any],
        config_effective_path: Path,
        config_effective_sha: str,
    ) -> Dict[str, Any]:
        provenance: Dict[str, Any] = {
            "segments": self._provenance_entry("05_polished.json", resolved.polished_path),
            "clean_text": self._provenance_entry(
                "clean.txt" if resolved.clean_txt_path else "05_polished.json",
                clean_text_path,
            ),
            "metrics": self._provenance_entry(
                "metrics.json" if resolved.metrics_path else "05_polished.json",
                metrics_path,
            ),
        }
        if chunk_source:
            provenance["chunks"] = self._provenance_entry(chunk_source.name, chunk_source)
        else:
            provenance["chunks"] = {"source": chunk_info.get("strategy"), "path": "generated", "sha256": None}
        config_sources = []
        base_path = self.config_bundle.base_path
        if base_path:
            config_sources.append(
                {"label": "base", "path": rel_path(base_path), "sha256": compute_file_sha256(base_path)}
            )
        override_path = self.config_bundle.doc_override_path
        if override_path:
            config_sources.append(
                {"label": "override", "path": rel_path(override_path), "sha256": compute_file_sha256(override_path)}
            )
        provenance["config"] = {
            "effective": {"path": rel_path(config_effective_path), "sha256": config_effective_sha},
            "sources": config_sources,
        }
        return provenance

    def _provenance_entry(self, source_name: str, path: Optional[Path]) -> Dict[str, Optional[str]]:
        return {
            "source": source_name,
            "path": rel_path(path),
            "sha256": compute_file_sha256(path),
        }

    def _log_effective_overrides(self) -> None:
        overrides = self.config_bundle.cli_overrides or {}
        if overrides:
            self.logger.info("Overrides CLI appliqués: %s", overrides)

    def _load_validated_glossary(self, work_dir: Path) -> List[Dict[str, str]]:
        candidate = work_dir / "rag.glossary.yaml"
        if not candidate.exists():
            return []
        rules = load_glossary_rules(candidate)
        if rules:
            self.logger.info("Glossaire validé détecté (%d règles): %s", len(rules), candidate)
        return rules

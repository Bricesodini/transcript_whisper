"""Artefact generation helpers for RAG export."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

from utils import PipelineError, stable_id, write_json

from . import PROJECT_ROOT, RAG_SCHEMA_VERSION
from .resolver import ResolvedPaths

SegmentRecord = Dict[str, Any]
ChunkRecord = Dict[str, Any]


def load_segments(resolved: ResolvedPaths, *, source_label: str = "05_polished") -> Tuple[List[SegmentRecord], Dict[str, Any]]:
    payload = json.loads(resolved.polished_path.read_text(encoding="utf-8"))
    raw_segments = payload.get("segments") or []
    segments: List[SegmentRecord] = []
    conf_values: List[float] = []
    for idx, raw in enumerate(raw_segments):
        start = round(float(raw.get("start", 0.0)), 3)
        end = round(float(raw.get("end", start)), 3)
        text = (raw.get("text_human") or raw.get("text") or "").strip()
        speaker = raw.get("speaker")
        confidence = _segment_confidence(raw)
        if confidence is not None:
            conf_values.append(confidence)
        segment_id = stable_id(str(resolved.polished_path), start, end, speaker)
        segments.append(
            {
                "segment_id": segment_id,
                "index": idx,
                "start": start,
                "end": end,
                "text": text,
                "speaker": speaker,
                "confidence": confidence,
                "word_count": _count_words(text),
            "source_ref": {
                "type": source_label,
                "path": rel_path(resolved.polished_path),
                "segment_index": idx,
            },
            }
        )
    if not segments:
        raise PipelineError(f"Aucun segment dans {resolved.polished_path}")
    language = payload.get("language") or "auto"
    duration = segments[-1]["end"]
    summary = _confidence_summary(conf_values)
    return segments, {"language": language, "duration": duration, "confidence": summary, "source": resolved.polished_path}


def _segment_confidence(segment: Dict[str, Any]) -> Optional[float]:
    if segment.get("confidence") is not None:
        return round(float(segment["confidence"]), 3)
    words = segment.get("words") or []
    scores = [float(item.get("score", 0.0)) for item in words if item.get("score") is not None]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 3)


def build_chunks_from_segments(
    segments: List[SegmentRecord],
    *,
    doc_id: str,
    doc_title: str,
    doc_lang: str,
    target_tokens: int,
    overlap_tokens: int,
    citation_cfg: Dict[str, Any],
    base_url: Optional[str],
    confidence_threshold: float,
) -> Tuple[List[ChunkRecord], Dict[str, Any]]:
    if not segments:
        return [], {}
    target_words = max(40, int(target_tokens or 320))
    overlap_words = max(0, int(overlap_tokens or 0))

    buffer: List[SegmentRecord] = []
    buffer_words = 0
    chunks: List[ChunkRecord] = []
    for segment in segments:
        buffer.append(segment)
        buffer_words += max(1, segment["word_count"])
        if buffer_words >= target_words:
            chunks.append(
                _make_chunk(buffer, doc_id, doc_title, doc_lang, citation_cfg, base_url, confidence_threshold, len(chunks))
            )
            buffer, buffer_words = _carry_overlap(buffer, overlap_words)

    if buffer:
        chunks.append(
            _make_chunk(buffer, doc_id, doc_title, doc_lang, citation_cfg, base_url, confidence_threshold, len(chunks))
        )
    stats = {
        "strategy": "segments_word_count",
        "target_words": target_words,
        "overlap_words": overlap_words,
        "generated": True,
    }
    return chunks, stats


def _carry_overlap(buffer: List[SegmentRecord], overlap_words: int) -> Tuple[List[SegmentRecord], int]:
    if overlap_words <= 0:
        return [], 0
    carried: List[SegmentRecord] = []
    words_total = 0
    for segment in reversed(buffer):
        carried.insert(0, segment)
        words_total += max(1, segment["word_count"])
        if words_total >= overlap_words:
            break
    return carried, words_total


def _make_chunk(
    chunk_segments: List[SegmentRecord],
    doc_id: str,
    doc_title: str,
    doc_lang: str,
    citation_cfg: Dict[str, Any],
    base_url: Optional[str],
    confidence_threshold: float,
    index: int,
) -> ChunkRecord:
    start = chunk_segments[0]["start"]
    end = chunk_segments[-1]["end"]
    text_parts = [seg["text"] for seg in chunk_segments if seg.get("text")]
    text = " ".join(part.strip() for part in text_parts if part.strip())
    segment_ids = [seg["segment_id"] for seg in chunk_segments]
    confidences = [seg["confidence"] for seg in chunk_segments if seg.get("confidence") is not None]
    confidence = round(sum(confidences) / len(confidences), 3) if confidences else None
    chunk_id = stable_id(doc_id, start, end, chunk_segments[0].get("speaker"))
    citation = build_citation(
        chunk_id,
        doc_title,
        start,
        end,
        citation_cfg=citation_cfg,
        base_url=base_url,
    )
    tags: List[str] = []
    if confidence is not None and confidence < confidence_threshold:
        tags.append("low_confidence")
    return {
        "chunk_id": chunk_id,
        "index": index,
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "text": text,
        "segment_ids": segment_ids,
        "confidence": confidence,
        "citation": citation,
        "tags": tags,
        "doc_id": doc_id,
        "lang": doc_lang,
    }


def build_citation(
    chunk_id: str,
    title: str,
    start: float,
    end: float,
    *,
    citation_cfg: Dict[str, Any],
    base_url: Optional[str],
) -> Dict[str, Any]:
    start_mmss = _format_mmss(start)
    end_mmss = _format_mmss(end)
    url_template = citation_cfg.get("url_template") or "{base_url}?t={start_s}s"
    base = base_url or citation_cfg.get("base_url") or ""
    mapping = _SafeDict(
        chunk_id=chunk_id,
        title=title,
        start_s=round(start, 3),
        end_s=round(end, 3),
        start_mmss=start_mmss,
        end_mmss=end_mmss,
        base_url=base,
    )
    url = url_template.format_map(mapping) if base or "{base_url}" not in url_template else ""
    text_format = citation_cfg.get("text_format") or "{title} [{start_mmss}-{end_mmss}]"
    markdown_format = citation_cfg.get("markdown_format") or "[Voir extrait]({url}) ({start_mmss}-{end_mmss})"
    return {
        "text": text_format.format_map(mapping),
        "markdown": markdown_format.format_map({**mapping, "url": url}),
        "url": url,
    }


def build_llm_chunks(
    chunks: List[ChunkRecord],
    *,
    doc_id: str,
    doc_title: str,
    doc_lang: str,
) -> List[Dict[str, Any]]:
    llm_chunks: List[Dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        before = chunks[idx - 1]["chunk_id"] if idx > 0 else None
        after = chunks[idx + 1]["chunk_id"] if idx + 1 < len(chunks) else None
        llm_chunks.append(
            {
                "chunk_id": chunk["chunk_id"],
                "content": chunk["text"],
                "metadata": {
                    "source_doc": {"title": doc_title, "doc_id": doc_id, "lang": doc_lang},
                    "timestamp": {"start_s": chunk["start"], "end_s": chunk["end"], "url_direct": chunk["citation"].get("url")},
                    "quality": {"confidence": chunk.get("confidence"), "verified": False},
                    "context_window": {"before": before, "after": after},
                },
                "embedding_id": None,
            }
        )
    return llm_chunks


def load_existing_chunks(
    path: Path,
    *,
    segments: List[SegmentRecord],
    doc_id: str,
    doc_title: str,
    doc_lang: str,
    citation_cfg: Dict[str, Any],
    base_url: Optional[str],
    confidence_threshold: float,
) -> Tuple[List[ChunkRecord], Dict[str, Any]]:
    raw_entries: List[Dict[str, Any]] = []
    if path.suffix == ".jsonl":
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            raw_entries.append(json.loads(line))
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and "chunks" in payload:
            raw_entries = list(payload["chunks"])
        elif isinstance(payload, list):
            raw_entries = payload
        else:
            raise PipelineError(f"Format chunks inconnu: {path}")
    normalized: List[ChunkRecord] = []
    for idx, raw in enumerate(raw_entries):
        start = float(raw.get("start") or raw.get("ts_start") or raw.get("t0") or 0.0)
        end = float(raw.get("end") or raw.get("ts_end") or raw.get("t1") or start)
        text = (raw.get("text") or raw.get("text_human") or raw.get("content") or "").strip()
        raw_conf = raw.get("confidence") or raw.get("confidence_mean") or raw.get("score")
        confidence = round(float(raw_conf), 3) if raw_conf is not None else None
        segment_ids = _segment_ids_for_range(segments, start, end)
        citation = build_citation(
            raw.get("id") or raw.get("chunk_id") or f"{doc_id}_{idx}",
            doc_title,
            start,
            end,
            citation_cfg=citation_cfg,
            base_url=base_url,
        )
        chunk_id = raw.get("id") or raw.get("chunk_id") or stable_id(doc_id, start, end)
        tags: List[str] = []
        if confidence is not None and confidence < confidence_threshold:
            tags.append("low_confidence")
        normalized.append(
            {
                "chunk_id": chunk_id,
                "index": idx,
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
                "text": text,
                "segment_ids": segment_ids,
                "confidence": confidence,
                "citation": citation,
                "tags": tags,
                "doc_id": doc_id,
                "lang": doc_lang,
            }
        )
    stats = {"strategy": "existing_chunks", "source_path": rel_path(path), "generated": False}
    return normalized, stats


def build_quality_report(
    segments: List[SegmentRecord],
    chunks: List[ChunkRecord],
    *,
    duration: float,
    quality_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    threshold = float(quality_cfg.get("threshold_warn") or 0.6)
    confidence_values = [chunk["confidence"] for chunk in chunks if chunk.get("confidence") is not None]
    above_threshold = [value for value in confidence_values if value >= threshold]
    pct_confident = round(len(above_threshold) / len(confidence_values), 3) if confidence_values else None
    coverage = 0.0
    if chunks and duration > 0:
        coverage = round(min(1.0, (chunks[-1]["end"] - chunks[0]["start"]) / duration), 3)
    seg_ids = {segment["segment_id"] for segment in segments}
    broken_refs = sum(1 for chunk in chunks if any(seg_id not in seg_ids for seg_id in chunk["segment_ids"]))
    report = {
        "pct_chunks_conf_gt_threshold": pct_confident,
        "coverage_time_pct": coverage,
        "broken_refs": broken_refs,
        "totals": {"segments": len(segments), "chunks": len(chunks)},
        "thresholds": {
            "confidence_warn": threshold,
            "confidence_error": float(quality_cfg.get("threshold_error") or threshold - 0.15),
        },
        "lexical_index_terms": None,
        "lexical_docs": None,
    }
    return report


def build_manifest(
    *,
    doc_id: str,
    doc_title: str,
    doc_lang: str,
    generated_at: str,
    resolved: ResolvedPaths,
    config_snapshot: Dict[str, Any],
    segments_info: Dict[str, Any],
    chunk_info: Dict[str, Any],
    stats: Dict[str, Any],
    warnings: List[str],
    options: Dict[str, Any],
    provenance: Dict[str, Any],
    config_effective_path: Optional[Path],
    config_effective_sha256: Optional[str],
    deterministic_mode: bool,
    timestamps_policy: str,
) -> Dict[str, Any]:
    manifest = {
        "rag_schema_version": RAG_SCHEMA_VERSION,
        "generated_at": generated_at,
        "doc_id": doc_id,
        "doc_title": doc_title,
        "lang": doc_lang,
        "source_media_path": str(resolved.media_path) if resolved.media_path else None,
        "sources": {
            "segments": {"path": rel_path(segments_info.get("source")), "kind": "05_polished"},
            "text": {"path": rel_path(resolved.clean_txt_path or resolved.polished_path), "kind": "clean_txt" if resolved.clean_txt_path else "polished"},
            "confidence": {"path": rel_path(resolved.metrics_path or resolved.polished_path), "kind": "metrics" if resolved.metrics_path else "segments"},
            "chunks": {"path": chunk_info.get("source_path") if not chunk_info.get("generated") else "generated", "kind": chunk_info.get("strategy")},
        },
        "config_snapshot": config_snapshot,
        "stats": stats,
        "warnings": warnings,
        "options": options,
        "provenance": provenance,
        "config_effective_path": rel_path(config_effective_path),
        "config_effective_sha256": config_effective_sha256,
        "deterministic_mode": deterministic_mode,
        "timestamps_policy": timestamps_policy,
    }
    return manifest


def build_stats(
    segments: List[SegmentRecord],
    chunks: List[ChunkRecord],
    *,
    duration: float,
    chunk_strategy: Dict[str, Any],
    config_hash: str,
) -> Dict[str, Any]:
    conf_values = [segment["confidence"] for segment in segments if segment.get("confidence") is not None]
    confidence_summary = _confidence_summary(conf_values)
    return {
        "nb_segments": len(segments),
        "nb_chunks": len(chunks),
        "duration_s": round(duration, 3),
        "confidence_summary": confidence_summary,
        "chunking": chunk_strategy,
        "config_hash": config_hash,
    }


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            fh.write("\n")


def write_readme(path: Path, *, doc_id: str, doc_title: str, generated_at: str, stats: Dict[str, Any], output_dir: Path) -> None:
    lines = [
        f"# RAG Export — {doc_title}",
        "",
        f"- Doc ID: `{doc_id}`",
        f"- Schéma: {RAG_SCHEMA_VERSION}",
        f"- Généré le: {generated_at}",
        f"- Segments: {stats['nb_segments']}",
        f"- Chunks: {stats['nb_chunks']}",
        f"- Durée (s): {stats['duration_s']}",
        "",
        "## Utilisation",
        "- `segments.jsonl` : segments normalisés (timestamps + texte)",
        "- `chunks.jsonl` : blocs prêts pour RAG (citations incluses)",
        "- `chunks_for_llm.jsonl` : format prompt-friendly (optionnel)",
        "- `lexical.sqlite` : index FTS5 pour recherche lexicale",
        "- `quality.json` : métriques de santé",
        "",
        f"Sorties enregistrées dans `{output_dir}`.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def build_sqlite_index(chunks: List[ChunkRecord], target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        target_path.unlink()
    conn = sqlite3.connect(target_path)
    try:
        conn.execute(
            "CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, doc_id TEXT, start REAL, end REAL, confidence REAL, text TEXT)"
        )
        conn.executemany(
            "INSERT INTO chunks(chunk_id, doc_id, start, end, confidence, text) VALUES (?, ?, ?, ?, ?, ?)",
            [(chunk["chunk_id"], chunk["doc_id"], chunk["start"], chunk["end"], chunk.get("confidence"), chunk["text"]) for chunk in chunks],
        )
        conn.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id, doc_id, text)")
        conn.executemany(
            "INSERT INTO chunks_fts(chunk_id, doc_id, text) VALUES (?, ?, ?)",
            [(chunk["chunk_id"], chunk["doc_id"], chunk["text"]) for chunk in chunks],
        )
        conn.execute(
            "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)"
        )
        conn.execute("INSERT INTO metadata(key, value) VALUES (?, ?)", ("schema_version", RAG_SCHEMA_VERSION))
        conn.commit()
    finally:
        conn.close()


def _confidence_summary(values: Sequence[float]) -> Optional[Dict[str, float]]:
    if not values:
        return None
    sorted_vals = sorted(values)
    pct = lambda p: sorted_vals[max(0, min(len(sorted_vals) - 1, int(math.floor(p * (len(sorted_vals) - 1)))))]
    return {
        "avg": round(mean(sorted_vals), 3),
        "min": round(sorted_vals[0], 3),
        "max": round(sorted_vals[-1], 3),
        "p05": round(pct(0.05), 3),
        "p95": round(pct(0.95), 3),
    }


def _segment_ids_for_range(segments: List[SegmentRecord], start: float, end: float) -> List[str]:
    ids: List[str] = []
    for segment in segments:
        if segment["end"] <= start:
            continue
        if segment["start"] >= end and ids:
            break
        if segment["start"] < end and segment["end"] > start:
            ids.append(segment["segment_id"])
    return ids


def _count_words(text: str) -> int:
    if not text:
        return 0
    return len(text.split())


def _format_mmss(value: float) -> str:
    seconds = max(0, int(round(value)))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def rel_path(path: Optional[Path]) -> Optional[str]:
    if not path:
        return None
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def compute_file_sha256(path: Optional[Path]) -> Optional[str]:
    if not path or not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_config_effective(path: Path, config: Dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(config, fh, allow_unicode=True, sort_keys=True)
    sha = compute_file_sha256(path)
    return sha or ""


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"

#!/usr/bin/env python3
"""
Post-export enhancer for ARTE-style transcripts.

Can be invoked manually or imported from the pipeline to:
  - recompute confidence metrics from Whisper word scores
  - clean punctuation artefacts
  - enrich JSONL exports with section titles
  - emit a paragraph-level JSONL and refreshed metrics/audit reports
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, NamedTuple, Optional, Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.append(str(SRC_DIR))

from text_cleaning import DEFAULT_GLOSSARY, clean_human_text, normalize_markdown_block, normalize_markdown_line
from validate_outputs import validate_export_bundle
SENTENCE_CONF_THRESHOLD = 0.6
SENTENCE_LOW_RATIO_THRESHOLD = 0.4


class Word(NamedTuple):
    start: float
    end: float
    score: float


@dataclass
class WordIndex:
    words: List[Word]

    def __post_init__(self) -> None:
        self.words.sort(key=lambda w: w.start)
        self.starts = [w.start for w in self.words]

    def scores_in_interval(self, start: float, end: float) -> List[float]:
        if not self.words or end <= start:
            return []
        idx = bisect_left(self.starts, start)
        while idx > 0 and self.words[idx - 1].end > start:
            idx -= 1
        scores: List[float] = []
        for word in self.words[idx:]:
            if word.start >= end:
                break
            if word.end > start and word.start < end:
                scores.append(word.score)
        return scores


def load_words(aligned_path: Path) -> WordIndex:
    payload = json.loads(aligned_path.read_text(encoding="utf-8"))
    words: List[Word] = []
    for segment in payload.get("segments", []):
        for word in segment.get("words") or []:
            try:
                words.append(Word(float(word["start"]), float(word["end"]), float(word["score"])))
            except (KeyError, TypeError, ValueError):
                continue
    return WordIndex(words=words)


def compute_confidence_stats(word_index: WordIndex, start: float, end: float, low_threshold: float = 0.5) -> Dict[str, Optional[float]]:
    scores = word_index.scores_in_interval(start, end)
    if not scores:
        return {"confidence_mean": None, "confidence_p05": None, "low_span_ratio": 0.0}
    scores_sorted = sorted(scores)
    avg = statistics.mean(scores_sorted)
    p05_index = max(0, int(math.floor(len(scores_sorted) * 0.05)) - 1)
    p05 = scores_sorted[p05_index]
    low_count = sum(1 for score in scores_sorted if score < low_threshold)
    low_span_ratio = low_count / len(scores_sorted)
    return {
        "confidence_mean": round(avg, 3),
        "confidence_p05": round(p05, 3),
        "low_span_ratio": round(low_span_ratio, 3),
    }




def load_jsonl(path: Path) -> List[Dict]:
    entries: List[Dict] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if raw:
                entries.append(json.loads(raw))
    return entries


def dump_jsonl(path: Path, rows: Iterable[Dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def update_sentence_confidence(sentence: Dict, stats: Dict[str, Optional[float]]) -> None:
    sentence.pop("low_span_ratio", None)
    sentence["confidence_mean"] = stats["confidence_mean"]
    sentence["confidence_p05"] = stats["confidence_p05"]
    sentence["low_span_ratio"] = stats["low_span_ratio"]
    duration = max(0.0, float(sentence.get("end", 0.0)) - float(sentence.get("start", 0.0)))
    sentence["low_duration"] = round(duration * stats["low_span_ratio"], 3) if duration else 0.0
    for field in ("text", "text_human"):
        value = sentence.get(field)
        if isinstance(value, str):
            sentence[field] = clean_human_text(value, glossary=DEFAULT_GLOSSARY)


def update_sections_payload(sections: List[Dict], word_index: WordIndex, low_threshold: float) -> None:
    for section in sections:
        stats = compute_confidence_stats(word_index, section.get("start", 0.0), section.get("end", 0.0), low_threshold=low_threshold)
        metadata = section.get("metadata") or {}
        metadata["avg_confidence"] = stats["confidence_mean"]
        metadata["confidence_p05"] = stats["confidence_p05"]
        metadata["low_span_ratio"] = stats["low_span_ratio"]
        section["metadata"] = metadata
        paragraph_text = section.get("paragraph")
        if isinstance(paragraph_text, str):
            section["paragraph"] = clean_human_text(paragraph_text, glossary=DEFAULT_GLOSSARY)
        paragraphs_field = section.get("paragraphs")
        if isinstance(paragraphs_field, list):
            for paragraph in paragraphs_field:
                if isinstance(paragraph, dict):
                    text_val = paragraph.get("text")
                    if isinstance(text_val, str):
                        paragraph["text"] = clean_human_text(text_val, glossary=DEFAULT_GLOSSARY)
        quotes_field = section.get("quotes")
        if isinstance(quotes_field, list):
            for idx, quote in enumerate(quotes_field):
                if isinstance(quote, dict):
                    quote_text = quote.get("text")
                    if isinstance(quote_text, str):
                        quote["text"] = clean_human_text(quote_text, glossary=DEFAULT_GLOSSARY)
                elif isinstance(quote, str):
                    quotes_field[idx] = clean_human_text(quote, glossary=DEFAULT_GLOSSARY)
        for sentence in section.get("sentences", []):
            sentence_stats = compute_confidence_stats(
                word_index,
                sentence.get("start", 0.0),
                sentence.get("end", 0.0),
                low_threshold=low_threshold,
            )
            update_sentence_confidence(sentence, sentence_stats)


def try_load_json(path: Path) -> Dict:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        marker = '"artifacts"'
        if marker in text:
            trimmed = text.split(marker)[0].rstrip()
            if trimmed.endswith(","):
                trimmed = trimmed[:-1]
            trimmed += "\n}"
            data = json.loads(trimmed)
            data["artifacts"] = {}
            return data
        raise


def percentile(values: Sequence[float], ratio: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    index = int(math.floor(len(values) * ratio))
    index = min(max(index, 0), len(values) - 1)
    return values[index]


def build_low_conf_rows(
    clean_entries: Sequence[Dict],
    *,
    doc_id: str,
    conf_threshold: Optional[float],
    ratio_threshold: Optional[float],
) -> List[Dict]:
    rows: List[Dict] = []
    total = len(clean_entries)
    for idx, entry in enumerate(clean_entries):
        conf_mean = entry.get("confidence_mean")
        low_ratio = entry.get("low_span_ratio")
        if conf_mean is None and low_ratio is None:
            continue
        is_low = False
        if conf_threshold is not None and conf_mean is not None and conf_mean < conf_threshold:
            is_low = True
        if not is_low and ratio_threshold is not None and low_ratio is not None and low_ratio > ratio_threshold:
            is_low = True
        if not is_low:
            continue
        prev_text = clean_entries[idx - 1].get("text_human") if idx > 0 else None
        next_text = clean_entries[idx + 1].get("text_human") if idx + 1 < total else None
        rows.append(
            {
                "id": f"{doc_id}#sent={entry.get('id') or idx}",
                "sentence_id": entry.get("id") or str(idx),
                "sentence_index": idx,
                "section_id": entry.get("section_id"),
                "chunk_id": entry.get("chunk_id"),
                "ts_start": entry.get("ts_start"),
                "ts_end": entry.get("ts_end"),
                "text": entry.get("text_human") or entry.get("text") or "",
                "mean_confidence": conf_mean,
                "confidence_p05": entry.get("confidence_p05"),
                "low_token_ratio": low_ratio,
                "prev_text": prev_text,
                "next_text": next_text,
            }
        )
    return rows


def _resolve_export_paths(export_dir: Path, base_name: Optional[str]) -> tuple[str, Dict[str, Path]]:
    if base_name:
        clean_jsonl = export_dir / f"{base_name}.clean.jsonl"
        if not clean_jsonl.exists():
            raise FileNotFoundError(f"Fichier introuvable: {clean_jsonl}")
    else:
        try:
            clean_jsonl = next(export_dir.glob("*.clean.jsonl"))
        except StopIteration:
            raise FileNotFoundError(f"Aucun *.clean.jsonl trouvé dans {export_dir}")
        base_name = clean_jsonl.name[: -len(".clean.jsonl")]
    paths = {
        "clean_jsonl": clean_jsonl,
        "clean_txt": export_dir / f"{base_name}.clean.txt",
        "chapters": export_dir / f"{base_name}.chapters.json",
        "chunks": export_dir / f"{base_name}.chunks.jsonl",
        "quotes": export_dir / f"{base_name}.quotes.jsonl",
        "metrics": export_dir / f"{base_name}.metrics.json",
        "audit": export_dir / f"{base_name}.audit.md",
        "paragraphs": export_dir / f"{base_name}.paragraphs.jsonl",
        "chunks_meta": export_dir / f"{base_name}.chunks.meta.json",
        "clean_md": export_dir / f"{base_name}.md",
        "low_conf": export_dir / f"{base_name}.low_confidence.jsonl",
    }
    return base_name, paths


def refresh_arte_outputs(
    work_dir: Path,
    export_dir: Path,
    *,
    doc_id: Optional[str] = None,
    low_threshold: float = 0.5,
    chunk_low_threshold: float = 0.1,
    logger=None,
) -> Dict[str, int]:
    work_dir = Path(work_dir)
    export_dir = Path(export_dir)
    base_name, export_paths = _resolve_export_paths(export_dir, doc_id)
    aligned_path = work_dir / "05_polished.json"
    structure_path = work_dir / "structure.json"
    log = (logger.info if logger else print)
    log(f"[ARTE refresh] Indexation des mots ({aligned_path})")
    word_index = load_words(aligned_path)
    if not word_index.words:
        raise RuntimeError("Aucun mot avec score trouvé, annulation.")

    log("[ARTE refresh] Mise à jour des sections")
    structure_data = json.loads(structure_path.read_text(encoding="utf-8"))
    update_sections_payload(structure_data.get("sections", []), word_index, low_threshold)
    structure_path.write_text(json.dumps(structure_data, ensure_ascii=False, indent=2), encoding="utf-8")

    chapters_data = json.loads(export_paths["chapters"].read_text(encoding="utf-8"))
    update_sections_payload(chapters_data.get("sections", []), word_index, low_threshold)
    section_titles = {section["section_id"]: section.get("title") for section in chapters_data.get("sections", [])}
    export_paths["chapters"].write_text(json.dumps(chapters_data, ensure_ascii=False, indent=2), encoding="utf-8")

    clean_entries = load_jsonl(export_paths["clean_jsonl"])
    document_source = clean_entries[0].get("source") if clean_entries else None
    for entry in clean_entries:
        entry.pop("low_span_ratio", None)
        stats = compute_confidence_stats(word_index, entry.get("ts_start", 0.0), entry.get("ts_end", 0.0), low_threshold)
        entry["confidence_mean"] = stats["confidence_mean"]
        entry["confidence_p05"] = stats["confidence_p05"]
        entry["low_span_ratio"] = stats["low_span_ratio"]
        section_id = entry.get("section_id")
        if section_id and section_id in section_titles:
            entry["section_title"] = section_titles[section_id]
        for field in ("text", "text_human"):
            value = entry.get(field)
            if isinstance(value, str):
                entry[field] = clean_human_text(value, glossary=DEFAULT_GLOSSARY)
    dump_jsonl(export_paths["clean_jsonl"], clean_entries)

    chunk_entries = load_jsonl(export_paths["chunks"])
    for chunk in chunk_entries:
        stats = compute_confidence_stats(word_index, chunk.get("start", 0.0), chunk.get("end", 0.0), low_threshold)
        chunk["confidence_mean"] = stats["confidence_mean"]
        chunk["confidence_p05"] = stats["confidence_p05"]
        chunk["low_span_ratio"] = stats["low_span_ratio"]
        section_ids = {sentence.get("section_id") for sentence in chunk.get("sentences", []) if sentence.get("section_id")}
        chunk["section_titles"] = sorted({section_titles[sid] for sid in section_ids if sid in section_titles})
        for sentence in chunk.get("sentences", []):
            sentence_stats = compute_confidence_stats(word_index, sentence.get("start", 0.0), sentence.get("end", 0.0), low_threshold)
            update_sentence_confidence(sentence, sentence_stats)
        for field in ("text", "text_human"):
            value = chunk.get(field)
            if isinstance(value, str):
                chunk[field] = clean_human_text(value, glossary=DEFAULT_GLOSSARY)
    dump_jsonl(export_paths["chunks"], chunk_entries)

    quote_entries = load_jsonl(export_paths["quotes"])
    for quote in quote_entries:
        section_id = quote.get("section_id")
        if section_id and section_id in section_titles:
            quote["section_title"] = section_titles[section_id]
        value = quote.get("text")
        if isinstance(value, str):
            quote["text"] = clean_human_text(value, glossary=DEFAULT_GLOSSARY)
    dump_jsonl(export_paths["quotes"], quote_entries)

    for path_key in ("clean_txt", "clean_md"):
        path = export_paths[path_key]
        if not path.exists():
            continue
        raw = path.read_text(encoding="utf-8")
        if path_key == "clean_md":
            normalized = normalize_markdown_block(raw, glossary=DEFAULT_GLOSSARY)
        else:
            lines = raw.splitlines()
            cleaned_lines = [normalize_markdown_line(line, glossary=DEFAULT_GLOSSARY) for line in lines]
            normalized = "\n".join(cleaned_lines)
        if normalized and not normalized.endswith("\n"):
            normalized += "\n"
        path.write_text(normalized, encoding="utf-8")

    doc_id = chunk_entries[0].get("document_id") if chunk_entries else None
    doc_id = doc_id or base_name

    low_conf_rows = build_low_conf_rows(
        clean_entries,
        doc_id=doc_id,
        conf_threshold=SENTENCE_CONF_THRESHOLD,
        ratio_threshold=SENTENCE_LOW_RATIO_THRESHOLD,
    )
    dump_jsonl(export_paths["low_conf"], low_conf_rows)

    paragraph_rows: List[Dict] = []
    for section in chapters_data.get("sections", []):
        metadata = section.get("metadata") or {}
        row = {
            "id": f"{doc_id}#section={section.get('section_id')}",
            "doc_id": doc_id,
            "source": document_source,
            "unit": "paragraph",
            "section_id": section.get("section_id"),
            "section_index": section.get("index"),
            "section_title": section.get("title"),
            "ts_start": section.get("start"),
            "ts_end": section.get("end"),
            "text": clean_human_text(section.get("paragraph") or "", glossary=DEFAULT_GLOSSARY),
            "lang": chapters_data.get("language"),
            "confidence_mean": metadata.get("avg_confidence"),
            "confidence_p05": metadata.get("confidence_p05"),
            "low_span_ratio": metadata.get("low_span_ratio"),
        }
        paragraph_rows.append(row)
    dump_jsonl(export_paths["paragraphs"], paragraph_rows)

    low_conf_path = export_paths["low_conf"]
    low_conf_bytes = low_conf_path.stat().st_size if low_conf_path.exists() else 0
    sentence_low_conf_count = len(low_conf_rows)

    metrics_data = try_load_json(export_paths["metrics"])
    metrics_data["phrases_total"] = len(clean_entries)
    metrics_data["chunks_total"] = len(chunk_entries)
    mid_conf = [chunk.get("confidence_mean") for chunk in chunk_entries if chunk.get("confidence_mean") is not None]
    metrics_data["chunk_confidence_mean"] = round(statistics.mean(mid_conf), 3) if mid_conf else None
    low_conf_chunks = [chunk for chunk in chunk_entries if chunk.get("low_span_ratio", 0) and chunk.get("low_span_ratio", 0) > chunk_low_threshold]
    chunk_low_conf_count = len(low_conf_chunks)
    metrics_data["low_conf_count"] = sentence_low_conf_count
    global_stats = compute_confidence_stats(word_index, word_index.words[0].start, word_index.words[-1].end, low_threshold)
    metrics_data["confidence"] = {
        "global_mean": global_stats["confidence_mean"],
        "global_p05": global_stats["confidence_p05"],
        "global_low_span_ratio": global_stats["low_span_ratio"],
    }
    artifacts = metrics_data.setdefault("artifacts", {})
    artifacts["low_confidence"] = {
        "path": low_conf_path.name,
        "bytes": low_conf_bytes,
        "count": sentence_low_conf_count,
    }
    export_paths["metrics"].write_text(json.dumps(metrics_data, ensure_ascii=False, indent=2), encoding="utf-8")

    sentence_means = [entry.get("confidence_mean") for entry in clean_entries if entry.get("confidence_mean") is not None]
    sentence_means_sorted = sorted(sentence_means)
    sentence_mean_val = round(statistics.mean(sentence_means), 3) if sentence_means else None
    sentence_p05_val = percentile(sentence_means_sorted, 0.05)
    chunk_thresh = chunk_low_threshold
    low_conf_chunks_verbose = [chunk for chunk in chunk_entries if chunk.get("low_span_ratio", 0) > chunk_thresh]
    audit_lines = [
        f"# Audit – {base_name}",
        "",
        "## Confidence Overview",
        f"- Sentence-level confidence: mean = {sentence_mean_val}, p05 = {sentence_p05_val} (n={len(sentence_means_sorted)})",
        f"- Low-confidence sentences (<{SENTENCE_CONF_THRESHOLD} or low_span_ratio > {SENTENCE_LOW_RATIO_THRESHOLD}): {sentence_low_conf_count}",
        f"- Chunk-level confidence mean: {metrics_data.get('chunk_confidence_mean')} over {len(chunk_entries)} chunks",
        f"- Chunks over low_span_ratio > {chunk_low_threshold}: {chunk_low_conf_count}",
        f"- Global word stats: mean = {global_stats['confidence_mean']}, p05 = {global_stats['confidence_p05']}, low_span_ratio = {global_stats['low_span_ratio']}",
        "",
        "## Low-conf spans",
    ]
    if low_conf_chunks_verbose:
        audit_lines.append(f"- Chunks with low_span_ratio > {chunk_thresh}: {len(low_conf_chunks_verbose)}")
        for chunk in low_conf_chunks_verbose:
            audit_lines.append(
                f"  - Chunk #{chunk.get('index')} [{chunk.get('start')}–{chunk.get('end')}] low_span_ratio={chunk.get('low_span_ratio')}"
            )
    else:
        audit_lines.append(f"- No chunks exceed low_span_ratio > {chunk_thresh}.")
    export_paths["audit"].write_text("\n".join(audit_lines) + "\n", encoding="utf-8")

    validate_export_bundle(export_dir, base_name)
    log(f"[ARTE refresh] Terminé ➜ {len(clean_entries)} phrases, {len(chunk_entries)} chunks, {len(paragraph_rows)} paragraphes.")
    return {
        "base_name": base_name,
        "clean_entries": len(clean_entries),
        "chunk_entries": len(chunk_entries),
        "paragraphs": len(paragraph_rows),
    }


def cli_main() -> None:
    parser = argparse.ArgumentParser(description="Recalcule les confiances et enrichit les exports ARTE.")
    parser.add_argument("--work-dir", type=Path, required=True, help="Répertoire work/… contenant structure.json + 05_polished.json")
    parser.add_argument("--export-dir", type=Path, required=True, help="Dossier exports/TRANSCRIPT - …")
    parser.add_argument("--doc-id", type=str, help="Nom de base du document (ex: « Titre.mp4 » sans extension).")
    parser.add_argument("--low-threshold", type=float, default=0.5, help="Seuil de probabilité pour les spans faibles.")
    parser.add_argument("--chunk-low-threshold", type=float, default=0.1, help="Seuil low_span_ratio pour compter les chunks low-conf.")
    args = parser.parse_args()

    refresh_arte_outputs(
        work_dir=args.work_dir,
        export_dir=args.export_dir,
        doc_id=args.doc_id,
        low_threshold=args.low_threshold,
        chunk_low_threshold=args.chunk_low_threshold,
    )


if __name__ == "__main__":
    cli_main()

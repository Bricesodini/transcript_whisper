# -*- coding: utf-8 -*-
import csv
import json
import os
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils import copy_to_clipboard

WORD_PATTERN = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ’'_-]+")
SECTION_TOLERANCE = 0.05


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\u00A0", " ")
    normalized = "".join(ch for ch in normalized if ch == "\n" or ord(ch) >= 32)
    return normalized


def _write_utf8(path: Path, text: str) -> None:
    normalized = _normalize_text(text)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(normalized)


class Exporter:
    def __init__(self, config: Dict, logger):
        self.logger = logger
        self.cfg = config.get("export", {})
        self.default_formats = config.get("defaults", {}).get("export_formats", ["txt", "md", "json", "srt", "vtt"])
        low_conf_cfg = self.cfg.get("low_confidence", {})
        self.low_conf_threshold: Optional[float] = None
        if low_conf_cfg.get("threshold") is not None:
            try:
                self.low_conf_threshold = float(low_conf_cfg.get("threshold"))
            except (TypeError, ValueError):
                self.logger.warning("Low-confidence threshold invalide, feature désactivée.")
                self.low_conf_threshold = None
        self.low_conf_formats = self._parse_low_conf_formats(low_conf_cfg.get("formats"))
        csv_threshold_value = low_conf_cfg.get("csv_threshold")
        try:
            self.low_conf_csv_threshold: Optional[float] = (
                float(csv_threshold_value)
                if csv_threshold_value is not None
                else (self.low_conf_threshold if self.low_conf_threshold is not None else 0.35)
            )
        except (TypeError, ValueError):
            self.logger.warning("CSV low-confidence threshold invalide, feature désactivée.")
            self.low_conf_csv_threshold = None
        self.low_conf_csv_enabled = bool(low_conf_cfg.get("csv_enabled", True))
        self.low_conf_csv_output = low_conf_cfg.get("csv_output")

    def _format_timestamp(self, seconds: float, separator: str = ",") -> str:
        ms = int(round((seconds - int(seconds)) * 1000))
        total = int(seconds)
        s = total % 60
        total //= 60
        m = total % 60
        h = total // 60
        if separator == ".":
            return f"{h:02}:{m:02}:{s:02}.{ms:03}"
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    def _write_txt(self, path: Path, segments: List[Dict]) -> str:
        lines = []
        for seg in segments:
            speaker = seg.get("speaker")
            prefix = f"{speaker}: " if speaker else ""
            text = self._render_segment_text(seg, "txt")
            lines.append(f"{prefix}{text}")
        text = "\n".join(lines)
        _write_utf8(path, text)
        return text

    def _write_md(self, path: Path, structure: Dict, segments: List[Dict]) -> None:
        lines = ["# Transcription structurée", ""]
        for section in structure.get("sections", []):
            start = self._format_timestamp(section["start"])
            title = section.get("auto_title") or section.get("title") or "Section"
            lines.append(f"## {title} ({start})")
            summary = section.get("auto_summary")
            if summary:
                lines.append(f"_{summary}_")
            lines.append("")
            paragraph = self._section_text_with_markup(section, segments, "md")
            lines.append(paragraph)
            if section["quotes"]:
                lines.append("")
                lines.append("### Citations clés")
                for quote in section["quotes"]:
                    lines.append(f"> {quote}")
            lines.append("")
        _write_utf8(path, "\n".join(lines).strip() + "\n")

    def _write_srt_vtt(self, path: Path, segments: List[Dict], fmt: str) -> None:
        separator = "," if fmt == "srt" else "."
        lines: List[str] = []
        if fmt == "vtt":
            lines.extend(["WEBVTT", ""])
        for idx, seg in enumerate(segments, 1):
            start = self._format_timestamp(seg["start"], separator=separator)
            end = self._format_timestamp(seg["end"], separator=separator)
            text = _normalize_text(seg["text"])
            if fmt == "srt":
                lines.extend([str(idx), f"{start} --> {end}", text, ""])
            else:
                lines.extend([f"{start} --> {end}", text, ""])
        text = "\n".join(lines).rstrip() + "\n\n"
        _write_utf8(path, text)

    def _write_json_export(self, path: Path, structure: Dict, segments: List[Dict], aligned_path: Path) -> None:
        payload = {
            "meta": {"language": structure.get("language"), "aligned": str(aligned_path)},
            "sections": structure.get("sections", []),
            "segments": segments,
        }
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=self.cfg.get("json_indent", 2))

    def _write_jsonl(self, path: Path, segments: List[Dict]) -> None:
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for segment in segments:
                handle.write(json.dumps(segment, ensure_ascii=False))
                handle.write("\n")

    def run(
        self,
        base_name: str,
        out_dir: Path,
        segments: List[Dict],
        structure: Dict,
        aligned_path: Path,
        formats: List[str] = None,
        parallel: Optional[bool] = None,
    ) -> Dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        target_formats = self._resolve_formats(formats or self.default_formats)
        artifacts: Dict[str, Path] = {}
        if not target_formats:
            return artifacts
        parallel_requested = parallel if parallel is not None else bool(self.cfg.get("parallel"))
        jobs = [(fmt, out_dir / f"{base_name}.{fmt}") for fmt in target_formats]
        if parallel_requested and len(jobs) > 1:
            max_workers = self._parallel_workers(len(jobs))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(
                        self._export_single_format,
                        fmt,
                        path,
                        base_name,
                        segments,
                        structure,
                        aligned_path,
                    ): fmt
                    for fmt, path in jobs
                }
                for future in as_completed(futures):
                    fmt_key, outfile = future.result()
                    artifacts[fmt_key] = outfile
        else:
            for fmt, path in jobs:
                fmt_key, outfile = self._export_single_format(fmt, path, base_name, segments, structure, aligned_path)
                artifacts[fmt_key] = outfile

        self._maybe_write_low_conf_csv(base_name, out_dir, segments)
        return artifacts

    def _resolve_formats(self, formats: List[str]) -> List[str]:
        ordered: List[str] = []
        for fmt in formats:
            fmt_key = str(fmt).strip().lower()
            if not fmt_key or fmt_key in ordered:
                continue
            ordered.append(fmt_key)
        return ordered

    def _parallel_workers(self, task_count: int) -> int:
        env_threads = os.environ.get("POST_THREADS")
        try:
            cap = int(env_threads) if env_threads else 0
        except ValueError:
            cap = 0
        logical = os.cpu_count() or 4
        limit = cap or logical
        return max(2, min(task_count, limit))

    def _export_single_format(
        self,
        fmt: str,
        outfile: Path,
        base_name: str,
        segments: List[Dict],
        structure: Dict,
        aligned_path: Path,
    ) -> Tuple[str, Path]:
        fmt_key = fmt.lower()
        if fmt_key == "txt":
            text = self._write_txt(outfile, segments)
            copy_to_clipboard(text, self.logger)
        elif fmt_key == "md":
            self._write_md(outfile, structure, segments)
        elif fmt_key == "json":
            self._write_json_export(outfile, structure, segments, aligned_path)
        elif fmt_key == "jsonl":
            self._write_jsonl(outfile, segments)
        elif fmt_key in {"srt", "vtt"}:
            self._write_srt_vtt(outfile, segments, fmt_key)
        elif fmt_key == "clean_txt":
            self._write_clean_txt(outfile, structure, segments)
        else:
            self.logger.warning("Format export inconnu: %s", fmt_key)
        return fmt_key, outfile

    def _parse_low_conf_formats(self, formats_cfg) -> Dict[str, str]:
        formats: Dict[str, str] = {}
        if isinstance(formats_cfg, dict):
            for fmt, style in formats_cfg.items():
                fmt_key = str(fmt).strip().lower()
                if fmt_key:
                    formats[fmt_key] = style
        elif isinstance(formats_cfg, list):
            for fmt in formats_cfg:
                fmt_key = str(fmt).strip().lower()
                if fmt_key:
                    formats[fmt_key] = "italics" if fmt_key == "md" else "brackets"
        elif isinstance(formats_cfg, str):
            fmt_key = formats_cfg.strip().lower()
            if fmt_key:
                formats[fmt_key] = "italics" if fmt_key == "md" else "brackets"
        return formats

    def _should_mark_low_conf(self, fmt: str) -> bool:
        return bool(self.low_conf_threshold is not None and fmt in self.low_conf_formats)

    def _render_segment_text(self, segment: Dict, fmt: str) -> str:
        text = segment.get("text", "")
        if not self._should_mark_low_conf(fmt):
            return text
        words = segment.get("words") or []
        style = self.low_conf_formats.get(fmt)
        return self._mark_low_conf_words(text, words, style)

    def _mark_low_conf_words(self, text: str, words: List[Dict], style) -> str:
        if not text or not words or self.low_conf_threshold is None:
            return text
        prefix, suffix, template = self._resolve_style(style)
        if not prefix and not suffix and not template:
            return text
        tokens = self._tokenize_text(text)
        threshold = float(self.low_conf_threshold)
        word_idx = 0
        pointer = 0
        while pointer < len(tokens):
            token = tokens[pointer]
            pointer += 1
            if not token["is_word"]:
                continue
            normalized_token = self._normalize_word(token["value"])
            if not normalized_token:
                continue
            while word_idx < len(words):
                candidate = words[word_idx]
                word_idx += 1
                candidate_word = candidate.get("word") or candidate.get("text") or ""
                normalized_candidate = self._normalize_word(candidate_word)
                if not normalized_candidate:
                    continue
                if normalized_candidate == normalized_token:
                    probability = candidate.get("probability")
                    try:
                        probability_value = float(probability) if probability is not None else None
                    except (TypeError, ValueError):
                        probability_value = None
                    if probability_value is not None and probability_value < threshold:
                        token["value"] = self._apply_style(token["value"], prefix, suffix, template)
                    break
        return "".join(token["value"] for token in tokens)

    def _tokenize_text(self, text: str) -> List[Dict]:
        tokens: List[Dict] = []
        last_idx = 0
        for match in WORD_PATTERN.finditer(text):
            if match.start() > last_idx:
                tokens.append({"value": text[last_idx:match.start()], "is_word": False})
            tokens.append({"value": match.group(0), "is_word": True})
            last_idx = match.end()
        if last_idx < len(text):
            tokens.append({"value": text[last_idx:], "is_word": False})
        return tokens

    def _normalize_word(self, value: str) -> str:
        if not value:
            return ""
        base = re.sub(r"[^0-9A-Za-zÀ-ÖØ-öø-ÿ]", "", value)
        return base.lower()

    def _resolve_style(self, style) -> Tuple[str, str, Optional[str]]:
        template = None
        if isinstance(style, dict):
            template = style.get("template")
            return style.get("prefix", ""), style.get("suffix", ""), template
        style_key = str(style).lower()
        if style_key in {"italic", "italics", "md-italics"}:
            return "_", "_", None
        if style_key in {"brackets", "square"}:
            return "[", "]", None
        if style_key in {"bold", "strong"}:
            return "**", "**", None
        if style_key in {"inline-code", "code"}:
            return "`", "`", None
        return "", "", template

    def _apply_style(self, word: str, prefix: str, suffix: str, template: Optional[str]) -> str:
        if template:
            try:
                return str(template).format(word=word)
            except Exception:
                return f"{prefix}{word}{suffix}"
        return f"{prefix}{word}{suffix}"

    def _section_text_with_markup(self, section: Dict, segments: List[Dict], fmt: str) -> str:
        if not self._should_mark_low_conf(fmt):
            return section.get("paragraph", "").strip()
        parts = []
        for seg in segments:
            if self._segment_in_section(seg, section):
                rendered = self._render_segment_text(seg, fmt).strip()
                if rendered:
                    parts.append(rendered)
        text = " ".join(parts).strip()
        return text or section.get("paragraph", "").strip()

    def _segment_in_section(self, segment: Dict, section: Dict) -> bool:
        start = float(section.get("start", 0.0))
        end = float(section.get("end", start))
        return (segment.get("start", 0.0) >= start - SECTION_TOLERANCE) and (
            segment.get("end", 0.0) <= end + SECTION_TOLERANCE
        )

    def _write_clean_txt(self, path: Path, structure: Dict, segments: List[Dict]) -> None:
        lines: List[str] = []
        for section in structure.get("sections", []):
            timestamp = self._format_timestamp(section["start"])
            title = section.get("auto_title") or section.get("title") or "Section"
            lines.append(f"{title} ({timestamp})")
            lines.append("")
            paragraph = self._section_text_with_markup(section, segments, "clean_txt")
            lines.append(paragraph)
            lines.append("")
        text = "\n".join(lines).strip() + "\n"
        _write_utf8(path, text)

    def _maybe_write_low_conf_csv(self, base_name: str, out_dir: Path, segments: List[Dict]) -> None:
        if not self.low_conf_csv_enabled or not segments:
            return
        threshold = self.low_conf_csv_threshold
        if threshold is None:
            return
        rows = self._collect_low_conf_rows(segments, threshold)
        clusters = self._build_low_conf_clusters(rows) if rows else []
        outfile = Path(self.low_conf_csv_output) if self.low_conf_csv_output else out_dir / f"{base_name}.low_confidence.csv"
        outfile.parent.mkdir(parents=True, exist_ok=True)
        with outfile.open("w", encoding="utf-8", newline="\n") as handle:
            writer = csv.writer(handle)
            writer.writerow(["word", "start_ms", "end_ms", "score", "segment_id"])
            for row in rows:
                writer.writerow(
                    [
                        row["word"],
                        row["start_ms"],
                        row["end_ms"],
                        f"{row['score']:.3f}",
                        row["segment_id"],
                    ]
                )
            for cluster in clusters:
                writer.writerow(
                    [
                        f"[cluster] {cluster['word']}",
                        cluster["start_ms"],
                        cluster["end_ms"],
                        f"{cluster['score']:.3f}",
                        cluster["segment_id"],
                    ]
                )
        if rows:
            self.logger.info("Low-confidence CSV ➜ %s (%d mots)", outfile, len(rows))
        else:
            self.logger.info("Low-confidence CSV ➜ %s (aucun mot sous %.3f)", outfile, threshold)

    def _collect_low_conf_rows(self, segments: List[Dict], threshold: float) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for seg_idx, segment in enumerate(segments):
            words = segment.get("words") or []
            for word in words:
                probability = word.get("probability")
                try:
                    score = float(probability)
                except (TypeError, ValueError, OverflowError):
                    continue
                if score >= threshold:
                    continue
                start = word.get("start", segment.get("start", 0))
                end = word.get("end", segment.get("end", start))
                try:
                    start_ms = int(round(float(start) * 1000))
                    end_ms = int(round(float(end) * 1000))
                except (TypeError, ValueError):
                    continue
                rows.append(
                    {
                        "word": (word.get("word") or word.get("text") or "").strip() or "?",
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "score": score,
                        "segment_id": seg_idx,
                    }
                )
        return rows

    def _build_low_conf_clusters(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not rows:
            return []
        clusters: List[Dict[str, Any]] = []
        current: List[Dict[str, Any]] = []
        last_end = None
        last_segment = None
        for row in rows:
            contiguous = (
                current
                and row["segment_id"] == last_segment
                and last_end is not None
                and row["start_ms"] <= last_end + 200
            )
            if contiguous:
                current.append(row)
            else:
                self._finalize_cluster(current, clusters)
                current = [row]
            last_end = row["end_ms"]
            last_segment = row["segment_id"]
        self._finalize_cluster(current, clusters)
        return clusters

    def _finalize_cluster(self, cluster_rows: List[Dict[str, Any]], clusters: List[Dict[str, Any]]) -> None:
        if len(cluster_rows) < 3:
            return
        text = " ".join(row["word"] for row in cluster_rows).strip()
        start_ms = cluster_rows[0]["start_ms"]
        end_ms = cluster_rows[-1]["end_ms"]
        avg_score = sum(row["score"] for row in cluster_rows) / len(cluster_rows)
        clusters.append(
            {
                "word": text,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "score": avg_score,
                "segment_id": cluster_rows[0]["segment_id"],
            }
        )

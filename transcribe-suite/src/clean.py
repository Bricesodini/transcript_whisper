import re
import unicodedata
from collections import deque
from difflib import SequenceMatcher
from typing import Any, Deque, Dict, List, Optional, Tuple

from glossary import GlossaryManager
from textnorm import TextNormalizer, join_text

WORD_REPEAT_PATTERN = re.compile(r"\b([\w'’\-]+)(\s+\1\b)+", re.IGNORECASE)


class Cleaner:
    def __init__(self, config: Dict, logger, glossary: Optional[GlossaryManager] = None):
        self.logger = logger
        self.lang_cfg = config.get("languages", {})
        self.clean_cfg = config.get("cleaning", {})
        self.glossary = glossary
        self.normalizer = TextNormalizer(config.get("numbers", {}), config.get("typography", {}))
        self.remove_fillers = self.clean_cfg.get("remove_fillers", False)
        self.clean_fillers = self.clean_cfg.get("fillers", {})
        self.capitalize_start = self.clean_cfg.get("capitalize_sentence_start", False)
        self.replacements: List[Tuple[str, str]] = [
            (pattern.strip(), replacement)
            for pattern, replacement in self.clean_cfg.get("fix_proper_nouns", [])
            if pattern and replacement
        ]
        self.auto_corrections: List[Tuple[str, str]] = [
            (pattern.strip(), replacement)
            for pattern, replacement in self.clean_cfg.get("auto_corrections", [])
            if pattern and replacement
        ]
        self.min_word_confidence: Optional[float] = self.clean_cfg.get("min_word_confidence")

        norm_cfg = self.clean_cfg.get("normalization", {})
        self.normalize_apostrophes = bool(norm_cfg.get("normalize_apostrophes", True))
        self.max_word_repeat = max(1, int(norm_cfg.get("max_word_repeat", 2)))
        self.tic_markers = [marker.strip() for marker in norm_cfg.get("tic_markers", []) if marker.strip()]

        merge_cfg = self.clean_cfg.get("merge_short_segments", {})
        self.merge_short_enabled = bool(merge_cfg.get("enabled", False))
        self.merge_short_max_duration = float(merge_cfg.get("max_duration", 0.8))
        self.merge_short_max_gap = float(merge_cfg.get("max_gap", 0.5))

        redundancy_cfg = self.clean_cfg.get("redundancy", {})
        self.redundancy_enabled = bool(redundancy_cfg.get("enabled", True))
        self.redundancy_similarity = float(redundancy_cfg.get("similarity", 0.92))
        self.redundancy_window = max(1, int(redundancy_cfg.get("window", 4)))
        self.redundancy_min_chars = max(6, int(redundancy_cfg.get("min_chars", 12)))
        self.redundancy_max_gap = float(redundancy_cfg.get("max_gap", 6.0))
        self.redundancy_whitelist = {
            phrase.strip().lower()
            for phrase in redundancy_cfg.get("whitelist_phrases", []) or []
            if phrase and phrase.strip()
        }

        confidence_cfg = self.clean_cfg.get("confidence", {})
        self.segment_confidence_threshold = confidence_cfg.get("segment_threshold")
        if self.segment_confidence_threshold is not None:
            self.segment_confidence_threshold = float(self.segment_confidence_threshold)
        self.drop_low_confidence_segments = bool(confidence_cfg.get("drop_segments", False))
        word_threshold = confidence_cfg.get("word_threshold")
        self.confidence_word_threshold = (
            float(word_threshold) if word_threshold is not None else self.min_word_confidence
        )

        self.audit_sample_size = max(1, int(self.clean_cfg.get("audit_sample_size", 5)))
        self._report: Dict[str, Any] = {}

        self._tic_patterns = [self._compile_marker(marker) for marker in self.tic_markers]
        self._filler_patterns: Dict[str, re.Pattern] = {}

    def _compile_marker(self, marker: str) -> re.Pattern:
        escaped = re.escape(marker.strip())
        escaped = re.sub(r"\\\s+", r"\\s+", escaped)
        return re.compile(rf"(?i)\b{escaped}\b")

    def _init_report(self, total: int) -> None:
        self._report = {
            "input_segments": total,
            "output_segments": 0,
            "short_merges": 0,
            "fillers_removed": 0,
            "word_replacements": 0,
            "auto_corrections": 0,
            "redundant_segments": 0,
            "redundancy_guarded": 0,
            "dropped_segments": 0,
            "low_confidence_segments": 0,
            "examples": {
                "dropped": [],
                "redundant": [],
                "redundancy_guard": [],
                "low_confidence": [],
                "replacements": [],
            },
        }

    def _remember(self, bucket: str, payload: Dict[str, Any]) -> None:
        examples = self._report.setdefault("examples", {}).setdefault(bucket, [])
        if len(examples) < self.audit_sample_size:
            examples.append(payload)

    def _strip_fillers(self, text: str, language: str) -> Tuple[str, int]:
        if not self.remove_fillers:
            return text, 0
        pattern = self._filler_patterns.get(language)
        fillers = self._get_fillers(language)
        if fillers and pattern is None:
            escaped = "|".join(re.escape(f) for f in fillers)
            pattern = re.compile(rf"\b({escaped})\b", re.IGNORECASE)
            self._filler_patterns[language] = pattern
        if not pattern:
            return text, 0
        updated, hits = pattern.subn(" ", text)
        return updated, hits

    def _get_fillers(self, language: str) -> List[str]:
        if language in self.clean_fillers:
            return self.clean_fillers[language]
        return self.lang_cfg.get(language, {}).get("fillers", [])

    def _remove_tics(self, text: str) -> Tuple[str, int]:
        if not self._tic_patterns:
            return text, 0
        total = 0
        updated = text
        for pattern in self._tic_patterns:
            updated, hits = pattern.subn(" ", updated)
            total += hits
        return updated, total

    def _dedupe_words(self, text: str) -> str:
        prev = None
        current = text
        while current and current != prev:
            prev = current
            current = WORD_REPEAT_PATTERN.sub(r"\1", current)
        return current

    def _sanitize_text(self, text: str, language: str) -> Tuple[str, int, int, int]:
        cleaned = unicodedata.normalize("NFC", text or "").strip()
        filler_hits = 0
        tic_hits = 0
        replacement_hits = 0
        if not cleaned:
            return "", filler_hits, tic_hits, replacement_hits
        cleaned, tic_hits = self._remove_tics(cleaned)
        cleaned, filler_hits = self._strip_fillers(cleaned, language)
        cleaned, fix_hits = self._apply_replacements(cleaned, self.replacements)
        cleaned, auto_hits = self._apply_replacements(cleaned, self.auto_corrections)
        replacement_hits = fix_hits + auto_hits
        cleaned = self._dedupe_words(cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        if self.normalize_apostrophes:
            cleaned = cleaned.replace("’", "'").replace("`", "'")
            cleaned = re.sub(r"\s+'", " '", cleaned)
            cleaned = re.sub(r"'\s+", "'", cleaned)
        cleaned = re.sub(r"\s+([,;:.!?])", r"\1", cleaned)
        cleaned = re.sub(r"\s+([»])", r"\1", cleaned)
        cleaned = re.sub(r"([«])\s+", r"\1", cleaned)
        cleaned = cleaned.strip(" -")
        if self.capitalize_start and cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]
        return cleaned.strip(), filler_hits, tic_hits, replacement_hits

    def _apply_replacements(self, text: str, replacements: List[Tuple[str, str]]) -> Tuple[str, int]:
        if not replacements or not text:
            return text, 0
        total = 0
        updated = text
        for pattern, replacement in replacements:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)
            updated, hits = regex.subn(replacement, updated)
            total += hits
        return updated, total

    def run(self, segments: List[Dict], language: str) -> List[Dict]:
        if not segments:
            self._init_report(0)
            return []

        min_duration = float(self.clean_cfg.get("min_segment_duration", 1.0))
        max_gap = float(self.clean_cfg.get("max_segment_gap", 1.2))

        self._init_report(len(segments))
        cleaned: List[Dict] = []
        buffer_seg = None
        redundancy_buffer: Deque[Dict[str, Any]] = deque(maxlen=self.redundancy_window)

        for seg in segments:
            raw_words = seg.get("words") or []
            low_conf_words = self._collect_low_conf_words(raw_words, self.confidence_word_threshold)
            words = self._filtered_words(raw_words)
            text, filler_hits, tic_hits, replacement_hits = self._sanitize_text(seg.get("text", ""), language)
            if filler_hits:
                self._report["fillers_removed"] += filler_hits
            if tic_hits:
                self._report["word_replacements"] += tic_hits
            if replacement_hits:
                self._report["auto_corrections"] += replacement_hits
                self._remember("replacements", {"start": seg.get("start"), "text": text[:80]})
            if not text:
                self._report["dropped_segments"] += 1
                self._remember("dropped", {"start": seg.get("start"), "reason": "empty"})
                continue
            lang_code = language or "fr"
            text_human, text_machine = self.normalizer.normalize_pair(text, lang_code)
            text = text_human

            normalized_for_similarity = self._normalize_for_similarity(text)
            if self._is_redundant(normalized_for_similarity, redundancy_buffer, seg["start"], seg["end"]):
                self._report["redundant_segments"] += 1
                self._remember("redundant", {"start": seg.get("start"), "text": text[:80]})
                continue

            redundancy_buffer.append({"text": normalized_for_similarity, "start": seg.get("start"), "end": seg.get("end")})
            if len(redundancy_buffer) > self.redundancy_window:
                redundancy_buffer.popleft()

            candidate = {
                "start": seg["start"],
                "end": seg["end"],
                "text": text_human,
                "text_human": text_human,
                "text_machine": text_machine,
                "speaker": seg.get("speaker"),
                "words": words,
                "language": lang_code,
                "text_fragments": [text_human],
            }
            if low_conf_words:
                candidate.setdefault("annotations", {})["low_conf_words"] = low_conf_words

            confidence = self._segment_confidence(words, seg)
            if confidence is not None:
                candidate["confidence"] = round(confidence, 3)
            if self.glossary:
                glossary_tokens = [(word.get("word") or word.get("text") or "").strip() for word in raw_words]
                self.glossary.ingest(text, glossary_tokens)

            if (
                self.segment_confidence_threshold is not None
                and confidence is not None
                and confidence < self.segment_confidence_threshold
            ):
                self._report["low_confidence_segments"] += 1
                details = {"start": seg.get("start"), "score": round(confidence, 3)}
                if self.drop_low_confidence_segments:
                    self._report["dropped_segments"] += 1
                    self._remember("dropped", {"start": seg.get("start"), "reason": "low_confidence"})
                    continue
                candidate.setdefault("flags", []).append("low_confidence")
                self._remember("low_confidence", details)

            seg_duration = seg["end"] - seg["start"]
            if buffer_seg:
                gap = seg["start"] - buffer_seg["end"]
                if seg_duration < min_duration and gap <= max_gap:
                    buffer_seg["end"] = seg["end"]
                    buffer_seg.setdefault("text_fragments", []).append(text)
                    buffer_seg["text"] = join_text(buffer_seg["text_fragments"])
                    buffer_seg.setdefault("words", []).extend(words)
                    self._refresh_dual_text(buffer_seg)
                    self._report["short_merges"] += 1
                    continue
            buffer_seg = candidate
            cleaned.append(buffer_seg)

        merged = self._merge_short_segments(cleaned)
        for seg in merged:
            seg.pop("text_fragments", None)
        self._report["output_segments"] = len(merged)
        if self.glossary:
            self._report["glossary"] = self.glossary.snapshot()
        return merged

    def _collect_low_conf_words(self, words: List[Dict], threshold: Optional[float]) -> List[Dict[str, Any]]:
        if threshold is None:
            return []
        flagged: List[Dict[str, Any]] = []
        for word in words:
            probability = word.get("probability")
            try:
                score = float(probability)
            except (TypeError, ValueError):
                continue
            if score >= threshold:
                continue
            flagged.append(
                {
                    "word": (word.get("word") or word.get("text") or "").strip(),
                    "score": round(score, 3),
                    "start": word.get("start"),
                    "end": word.get("end"),
                }
            )
        return flagged[: self.audit_sample_size]

    def _segment_confidence(self, words: List[Dict], original_seg: Dict) -> Optional[float]:
        scores: List[float] = []
        for word in words:
            probability = word.get("probability")
            try:
                scores.append(float(probability))
            except (TypeError, ValueError):
                continue
        if scores:
            return sum(scores) / len(scores)
        confidence = original_seg.get("confidence")
        if confidence is not None:
            try:
                return float(confidence)
            except (TypeError, ValueError):
                return None
        avg_logprob = original_seg.get("avg_logprob")
        if avg_logprob is not None:
            try:
                return max(0.0, min(1.0, (float(avg_logprob) + 5.0) / 5.0))
            except (TypeError, ValueError):
                return None
        return None

    def _filtered_words(self, words: List[Dict]) -> List[Dict]:
        if self.min_word_confidence is None:
            return [dict(word) for word in words]
        threshold = float(self.min_word_confidence)
        filtered = []
        for word in words:
            prob = word.get("probability", 1.0)
            try:
                keep = prob is None or float(prob) >= threshold
            except (TypeError, ValueError):
                keep = True
            if keep:
                filtered.append(dict(word))
        return filtered

    def _merge_short_segments(self, segments: List[Dict]) -> List[Dict]:
        if not self.merge_short_enabled or not segments:
            return segments
        merged: List[Dict] = []
        for seg in segments:
            if (
                merged
                and seg.get("speaker") == merged[-1].get("speaker")
                and (seg["start"] - merged[-1]["end"]) <= self.merge_short_max_gap
                and (seg["end"] - seg["start"]) <= self.merge_short_max_duration
            ):
                merged[-1]["end"] = seg["end"]
                merged[-1].setdefault("text_fragments", []).extend(seg.get("text_fragments", [seg.get("text")]))
                merged[-1]["text"] = join_text(merged[-1]["text_fragments"])
                merged[-1].setdefault("words", []).extend(seg.get("words", []))
                self._report["short_merges"] += 1
                self._refresh_dual_text(merged[-1])
            else:
                merged.append(seg)
        return merged

    def _refresh_dual_text(self, segment: Dict) -> None:
        language = segment.get("language") or "fr"
        fragments = segment.get("text_fragments")
        base_text = join_text(fragments) if fragments else segment.get("text", "")
        segment["text"] = base_text
        human, machine = self.normalizer.normalize_pair(base_text, language)
        segment["text"] = human
        segment["text_human"] = human
        segment["text_machine"] = machine

    def _normalize_for_similarity(self, text: str) -> str:
        normalized = text.lower()
        normalized = re.sub(r"[^a-z0-9à-öø-ÿ\s]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.strip()

    def _is_redundant(self, text: str, buffer: Deque[Dict[str, Any]], start: float, end: float) -> bool:
        if not self.redundancy_enabled or len(text) < self.redundancy_min_chars:
            return False
        lowered = text.lower()
        for phrase in self.redundancy_whitelist:
            if phrase and phrase in lowered:
                return False
        for previous in buffer:
            score = SequenceMatcher(None, text, previous["text"]).ratio()
            if score >= self.redundancy_similarity:
                gap = start - previous.get("end", start)
                if gap <= self.redundancy_max_gap:
                    return True
                self._report["redundancy_guarded"] += 1
                self._remember("redundancy_guard", {"start": start, "text": text[:80]})
        return False

    def report(self) -> Dict[str, Any]:
        snapshot = dict(self._report)
        examples = snapshot.get("examples", {})
        snapshot["examples"] = {key: list(values) for key, values in examples.items()}
        if "glossary" in snapshot:
            snapshot["glossary"] = list(snapshot["glossary"])
        return snapshot

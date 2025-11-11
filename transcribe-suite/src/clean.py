import re
from typing import Dict, List, Optional, Tuple


class Cleaner:
    def __init__(self, config: Dict, logger):
        self.logger = logger
        self.lang_cfg = config.get("languages", {})
        self.clean_cfg = config.get("cleaning", {})
        self.remove_fillers = self.clean_cfg.get("remove_fillers", False)
        self.clean_fillers = self.clean_cfg.get("fillers", {})
        self.capitalize_start = self.clean_cfg.get("capitalize_sentence_start", False)
        self.replacements: List[Tuple[str, str]] = [
            (pattern.strip(), replacement)
            for pattern, replacement in self.clean_cfg.get("fix_proper_nouns", [])
            if pattern and replacement
        ]
        self.min_word_confidence: Optional[float] = self.clean_cfg.get("min_word_confidence")
        merge_cfg = self.clean_cfg.get("merge_short_segments", {})
        self.merge_short_enabled = bool(merge_cfg.get("enabled", False))
        self.merge_short_max_duration = float(merge_cfg.get("max_duration", 0.8))
        self.merge_short_max_gap = float(merge_cfg.get("max_gap", 0.5))

    def _get_fillers(self, language: str) -> List[str]:
        if language in self.clean_fillers:
            return self.clean_fillers[language]
        return self.lang_cfg.get(language, {}).get("fillers", [])

    def _sanitize_text(self, text: str, language: str) -> str:
        cleaned = text.strip()
        if self.remove_fillers:
            fillers = self._get_fillers(language)
            if fillers:
                pattern = r"(" + "|".join(re.escape(f) for f in fillers) + r")"
                cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned)
        cleaned = self._apply_replacements(cleaned)
        cleaned = re.sub(r"\b(" + "|".join(re.escape(f) for f in self._get_fillers(language)) + r")\b", " ", cleaned, flags=re.IGNORECASE)
        if self.capitalize_start and cleaned:
            cleaned = cleaned[0].upper() + cleaned[1:]
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        return cleaned

    def _apply_replacements(self, text: str) -> str:
        updated = text
        for pattern, replacement in self.replacements:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)
            updated = regex.sub(replacement, updated)
        return updated

    def run(self, segments: List[Dict], language: str) -> List[Dict]:
        if not segments:
            return []
        min_duration = float(self.clean_cfg.get("min_segment_duration", 1.0))
        max_gap = float(self.clean_cfg.get("max_segment_gap", 1.2))

        cleaned: List[Dict] = []
        buffer = None

        for seg in segments:
            words = seg.get("words", [])
            text = self._sanitize_text(seg.get("text", ""), language)
            if not text:
                continue
            seg_duration = seg["end"] - seg["start"]
            if buffer:
                gap = seg["start"] - buffer["end"]
                if seg_duration < min_duration and gap <= max_gap:
                    buffer["end"] = seg["end"]
                    buffer["text"] = f"{buffer['text']} {text}".strip()
                    buffer.setdefault("words", []).extend(words)
                    continue
            buffer = {
                "start": seg["start"],
                "end": seg["end"],
                "text": text,
                "speaker": seg.get("speaker"),
                "words": words,
            }
            cleaned.append(buffer)

        return self._merge_short_segments(cleaned)

    def _filtered_words(self, words: List[Dict]) -> List[Dict]:
        if self.min_word_confidence is None:
            return [dict(word) for word in words]
        threshold = float(self.min_word_confidence)
        filtered = []
        for word in words:
            prob = word.get("probability", 1.0)
            if prob is None or prob >= threshold:
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
                merged[-1]["text"] = f"{merged[-1]['text']} {seg['text']}".strip()
                merged[-1].setdefault("words", []).extend(seg.get("words", []))
            else:
                merged.append(seg)
        return merged

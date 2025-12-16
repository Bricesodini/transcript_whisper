import re
import textwrap
from typing import Any, Dict, List, Optional

from textnorm import join_text
from utils import stable_id


class Structurer:
    def __init__(self, config: Dict, logger):
        self.logger = logger
        self.cfg = config.get("structure", {})
        self.trim_titles = self.cfg.get("trim_section_titles", False)
        self.title_case = str(self.cfg.get("title_case", "none")).lower()
        self._title_case_warned = False
        self.enable_titles = bool(self.cfg.get("enable_titles", True))
        soft_min = self.cfg.get("soft_min_duration")
        try:
            self.soft_min_duration = float(soft_min) if soft_min else None
        except (TypeError, ValueError):
            self.logger.warning("structure.soft_min_duration invalide, ignoré.")
            self.soft_min_duration = None
        confidence_cfg = config.get("cleaning", {}).get("confidence", {})
        self.sentence_threshold = float(confidence_cfg.get("sentence_threshold", confidence_cfg.get("segment_threshold", 0.55) or 0.55))

    def _new_section(self, index: int):
        return {"index": index, "start": None, "end": None, "segments": [], "text": [], "sentences_data": []}

    def _title_from_text(self, text: str) -> str:
        sentence = re.split(r"(?<=[.!?])\s+", text.strip())
        head = sentence[0] if sentence else text
        return head[:80].strip().rstrip(".") or "Section"

    def _extract_quotes(self, text: str) -> List[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        quotes = []
        for sent in sentences:
            sent = sent.strip()
            if 30 <= len(sent) <= 180:
                quotes.append(sent)
            if len(quotes) >= 3:
                break
        return quotes

    def run(self, segments: List[Dict], language: str, source_id: str, confidence_threshold: Optional[float] = None) -> Dict:
        if not segments:
            return {"sections": [], "language": language}
        threshold = confidence_threshold if confidence_threshold is not None else self.sentence_threshold

        target = float(self.cfg.get("target_section_duration", 180))
        maximum = float(self.cfg.get("max_section_duration", 480))
        min_gap = float(self.cfg.get("min_pause_gap", 6))

        sections: List[Dict] = []
        section = self._new_section(len(sections))
        last_end = None

        for seg in segments:
            if section["start"] is None:
                section["start"] = seg["start"]
            section["end"] = seg["end"]
            section["segments"].append(seg)
            section["text"].append(seg.get("text_human") or seg.get("text") or "")
            section["sentences_data"].extend(self._segment_sentences(seg))
            duration = section["end"] - section["start"]
            gap = seg["start"] - last_end if last_end is not None else 0
            last_end = seg["end"]

            should_close = False
            if duration >= maximum:
                should_close = True
            elif duration >= target and gap >= min_gap:
                should_close = True
            elif self.soft_min_duration and duration >= self.soft_min_duration:
                should_close = True

            if should_close:
                sections.append(section)
                section = self._new_section(len(sections))

        if section["segments"]:
            sections.append(section)

        for sec in sections:
            paragraph = " ".join(sec["text"]).strip()
            sec["paragraph"] = paragraph
            if self.enable_titles:
                sec["title"] = self._format_title(paragraph)
            sec["quotes"] = self._extract_quotes(paragraph)
            sec["duration"] = sec["end"] - sec["start"]
            sentences = sec.pop("sentences_data")
            section_id = stable_id(source_id, sec["start"], sec["end"])
            sec["sentences"] = sentences
            sec["paragraphs"] = self._sentences_to_paragraphs(sentences)
            sec["metadata"] = self._section_metadata(sentences)
            sec["section_id"] = section_id
            for sentence in sentences:
                sentence["section_id"] = section_id
                sentence.setdefault("language", language)
            sec["metadata"]["low_span_ratio"] = self._section_low_span_ratio(sentences, sec["duration"])
            del sec["text"]
            del sec["segments"]

        return {"sections": sections, "language": language}

    def _format_title(self, text: str) -> str:
        raw_title = self._title_from_text(text)
        if self.trim_titles:
            raw_title = textwrap.shorten(raw_title, width=80, placeholder="…")
        if raw_title and self.title_case in {"sentence", "title"}:
            if self.title_case == "title" and not self._title_case_warned:
                self.logger.info("structure.title_case='title' n'applique plus Title Case, utilisation du mode phrase.")
                self._title_case_warned = True
            raw_title = raw_title[0].upper() + raw_title[1:]
        return raw_title or "Section"

    def _segment_sentences(self, segment: Dict) -> List[Dict]:
        text = (segment.get("text_human") or segment.get("text") or "").strip()
        if not text:
            return []
        sentences = self._split_sentences(text)
        if not sentences:
            sentences = [text]
        words = [dict(word) for word in segment.get("words", []) or []]
        grouped_words = self._assign_words_to_sentences(words, sentences)

        total_chars = sum(len(sentence) for sentence in sentences) or 1
        duration = max(0.0, segment["end"] - segment["start"])
        cursor = segment["start"]
        enriched: List[Dict] = []
        for idx, sentence in enumerate(sentences):
            ratio = len(sentence) / total_chars
            delta = duration * ratio if duration > 0 else 0
            start = cursor
            end = cursor + delta
            cursor = end
            stats = self._compute_sentence_confidence(grouped_words[idx])
            human_text = sentence.strip()
            enriched.append(
                {
                    "text": human_text,
                    "text_human": human_text,
                    "text_machine": segment.get("text_machine", "").strip(),
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "speaker": segment.get("speaker"),
                    "confidence_mean": stats["mean"],
                    "confidence_p05": stats["p05"],
                    "low_duration": stats["low_duration"],
                    "tokens": stats["tokens"],
                    "language": segment.get("language"),
                }
            )
        if enriched:
            enriched[-1]["end"] = segment["end"]
        return enriched

    def _split_sentences(self, text: str) -> List[str]:
        parts = re.split(r"(?<=[.!?…])\s+", text)
        return [part.strip() for part in parts if part and part.strip()]

    def _sentences_to_paragraphs(self, sentences: List[Dict]) -> List[Dict]:
        if not sentences:
            return []
        paragraphs: List[Dict] = []
        current: Optional[Dict] = None
        for sentence in sentences:
            speaker = sentence.get("speaker") or "SPEAKER_00"
            if current and current["speaker"] == speaker:
                current["end"] = sentence["end"]
                current["text"] = join_text([current.get("text"), sentence.get("text")])
            else:
                current = {
                    "speaker": speaker,
                    "start": sentence["start"],
                    "end": sentence["end"],
                    "text": sentence["text"],
                }
                paragraphs.append(current)
        return paragraphs

    def _section_metadata(self, sentences: List[Dict]) -> Dict[str, Any]:
        speakers: Dict[str, int] = {}
        confidences: List[float] = []
        for sentence in sentences:
            speaker = sentence.get("speaker") or "SPEAKER_00"
            speakers[speaker] = speakers.get(speaker, 0) + 1
            score = sentence.get("confidence_mean")
            if score is not None:
                try:
                    confidences.append(float(score))
                except (TypeError, ValueError):
                    continue
        confidences.sort()
        avg_conf = round(sum(confidences) / len(confidences), 3) if confidences else None
        p05 = round(confidences[int(len(confidences) * 0.05)], 3) if confidences else None
        return {
            "avg_confidence": avg_conf,
            "confidence_p05": p05,
            "speaker_histogram": speakers,
            "sentence_count": len(sentences),
        }

    def _section_low_span_ratio(self, sentences: List[Dict], duration: float) -> Optional[float]:
        if not sentences or duration <= 0:
            return None
        low_duration = sum(sentence.get("low_duration", 0.0) for sentence in sentences)
        return round(min(1.0, max(0.0, low_duration / duration)), 3)

    def _assign_words_to_sentences(self, words: List[Dict], sentences: List[str]) -> List[List[Dict]]:
        if not sentences:
            return []
        total_words = len(words)
        if total_words == 0:
            return [[] for _ in sentences]
        total_chars = sum(len(sentence) for sentence in sentences) or 1
        assigned: List[List[Dict]] = []
        pointer = 0
        for idx, sentence in enumerate(sentences):
            chars = len(sentence)
            remaining_sentences = len(sentences) - idx
            remaining_words = total_words - pointer
            if remaining_sentences <= 1:
                take = remaining_words
            else:
                proportion = chars / total_chars
                take = int(round(total_words * proportion))
                min_remaining = remaining_sentences - 1
                take = max(0, min(remaining_words - min_remaining, take))
            slice_words = words[pointer : pointer + take]
            assigned.append(slice_words)
            pointer += take
        if pointer < total_words:
            assigned[-1].extend(words[pointer:])
        return assigned

    def _compute_sentence_confidence(self, words: List[Dict]) -> Dict[str, Any]:
        if not words:
            return {"mean": None, "p05": None, "low_duration": 0.0, "tokens": 0}
        probs: List[float] = []
        durations: List[float] = []
        low_duration = 0.0
        threshold = self.sentence_threshold
        for word in words:
            prob = word.get("probability")
            try:
                prob = float(prob) if prob is not None else None
            except (TypeError, ValueError):
                prob = None
            start = word.get("start")
            end = word.get("end")
            try:
                dur = max(0.01, float(end) - float(start))
            except (TypeError, ValueError):
                dur = 0.3
            durations.append(dur)
            probs.append(prob if prob is not None else 1.0)
            if prob is not None and prob < threshold:
                low_duration += dur
        weighted = sum(p * d for p, d in zip(probs, durations))
        total_dur = sum(durations) or 1.0
        mean = round(weighted / total_dur, 3)
        sorted_probs = sorted(p for p in probs if p is not None)
        p05 = round(sorted_probs[int(len(sorted_probs) * 0.05)], 3) if sorted_probs else None
        return {"mean": mean, "p05": p05, "low_duration": low_duration, "tokens": len(words)}

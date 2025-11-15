import math
from typing import Any, Dict, List, Optional, Tuple

from textnorm import join_text

from utils import stable_id


class Chunker:
    def __init__(self, cfg: Dict[str, Any], logger):
        self.cfg = cfg or {}
        self.logger = logger
        self.enabled = bool(self.cfg.get("enabled", True))
        self.target_tokens = int(self.cfg.get("target_tokens", 320))
        self.max_tokens = int(self.cfg.get("max_tokens", 420))
        self.min_tokens = int(self.cfg.get("min_tokens", 180))
        self.min_sentences = max(1, int(self.cfg.get("min_sentences", 2)))
        self.overlap_sentences = max(0, int(self.cfg.get("overlap_sentences", 1)))
        self.low_span_threshold = float(self.cfg.get("low_span_threshold", 0.3))

    def run(self, structure: Dict[str, Any], language: str, document_id: str) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        sentences = self._collect_sentences(structure)
        if not sentences:
            return []
        chunks = self._build_chunks(sentences, language, document_id)
        self.logger.info("Chunker: %d phrases ➜ %d blocs (%s)", len(sentences), len(chunks), language)
        return chunks

    def _collect_sentences(self, structure: Dict[str, Any]) -> List[Dict[str, Any]]:
        sentences: List[Dict[str, Any]] = []
        for section in structure.get("sections", []):
            for sentence in section.get("sentences", []):
                enriched = dict(sentence)
                enriched["section_index"] = section.get("index")
                sentences.append(enriched)
        return sentences

    def _build_chunks(self, sentences: List[Dict[str, Any]], language: str, document_id: str) -> List[Dict[str, Any]]:
        chunks: List[Dict[str, Any]] = []
        buffer: List[Dict[str, Any]] = []
        token_total = 0
        chunk_index = 0
        last_chunk_id: Optional[str] = None

        for sentence in sentences:
            enriched = dict(sentence)
            enriched["tokens"] = enriched.get("tokens") or self._estimate_tokens(enriched.get("text", ""))
            buffer.append(enriched)
            token_total += enriched["tokens"]
            if self._should_close_chunk(buffer, token_total):
                chunk = self._make_chunk(chunk_index, buffer, language, document_id, last_chunk_id)
                chunk_index += 1
                chunks.append(chunk)
                last_chunk_id = chunk["id"]
                buffer, token_total = self._carry_overlap(buffer, last_chunk_id)

        if buffer:
            chunks.append(self._make_chunk(chunk_index, buffer, language, document_id, last_chunk_id))
        return chunks

    def _should_close_chunk(self, buffer: List[Dict[str, Any]], token_total: int) -> bool:
        if not buffer:
            return False
        if len(buffer) < self.min_sentences:
            return False
        if token_total < self.min_tokens:
            return False
        if token_total >= self.max_tokens:
            return True
        if token_total >= self.target_tokens:
            return True
        return False

    def _carry_overlap(self, buffer: List[Dict[str, Any]], parent_chunk_id: Optional[str]) -> Tuple[List[Dict[str, Any]], int]:
        if self.overlap_sentences <= 0:
            return [], 0
        overlap = buffer[-self.overlap_sentences :] if len(buffer) >= self.overlap_sentences else buffer[:]
        carried: List[Dict[str, Any]] = []
        for sentence in overlap:
            duplicated = dict(sentence)
            duplicated["overlap"] = True
            if parent_chunk_id:
                duplicated["parent_chunk_id"] = parent_chunk_id
            carried.append(duplicated)
        token_total = sum(sentence["tokens"] for sentence in carried)
        return carried, token_total

    def _make_chunk(
        self,
        index: int,
        sentences: List[Dict[str, Any]],
        language: str,
        document_id: str,
        last_chunk_id: Optional[str],
    ) -> Dict[str, Any]:
        start = sentences[0].get("start", 0.0)
        end = sentences[-1].get("end", start)
        speaker_histogram: Dict[str, int] = {}
        text_parts: List[str] = []
        text_machine_parts: List[str] = []
        section_ids: set = set()
        token_total = 0
        low_duration_total = 0.0
        confidence_values: List[float] = []
        tokens_weighted: List[Tuple[float, int]] = []
        speaker_sequence: List[str] = []
        for sentence in sentences:
            speaker = sentence.get("speaker") or "SPEAKER_00"
            speaker_histogram[speaker] = speaker_histogram.get(speaker, 0) + 1
            speaker_sequence.append(speaker)
            section_id = sentence.get("section_id") or sentence.get("section_index")
            if section_id is not None:
                section_ids.add(str(section_id))
            token_total += sentence.get("tokens", 0)
            snippet = sentence.get("text", "").strip()
            snippet_machine = sentence.get("text_machine", "").strip()
            if snippet:
                text_parts.append(snippet)
            if snippet_machine:
                text_machine_parts.append(snippet_machine)
            confidence = sentence.get("confidence_mean")
            if confidence is not None:
                confidence_values.append(confidence)
                tokens_weighted.append((confidence, max(1, sentence.get("tokens", 1))))
            low_duration_total += sentence.get("low_duration", 0.0)
        chunk_text = join_text(text_parts)
        chunk_text_machine = join_text(text_machine_parts)
        duration = round(float(end) - float(start), 3)
        speaker_majority = max(speaker_histogram.items(), key=lambda item: item[1])[0] if speaker_histogram else "SPEAKER_00"
        speaker_switches = sum(1 for i in range(1, len(speaker_sequence)) if speaker_sequence[i] != speaker_sequence[i - 1])
        chunk_id = stable_id(document_id, start, end, speaker_majority)
        confidence_mean = (
            round(sum(val * weight for val, weight in tokens_weighted) / sum(weight for _, weight in tokens_weighted), 3)
            if tokens_weighted
            else None
        )
        confidence_values.sort()
        confidence_p05 = (
            round(confidence_values[int(len(confidence_values) * 0.05)], 3) if confidence_values else None
        )
        low_span_ratio = round(min(1.0, max(0.0, low_duration_total / duration)), 3) if duration > 0 else 0.0
        parent_ids_prev = sorted(
            {sentence.get("parent_chunk_id") for sentence in sentences if sentence.get("overlap") and sentence.get("parent_chunk_id")}
        )
        return {
            "schema_version": "1.0.0",
            "id": chunk_id,
            "index": index,
            "document_id": document_id,
            "language": language,
            "start": round(float(start), 3),
            "end": round(float(end), 3),
            "duration": duration,
            "token_count": token_total,
            "sentence_count": len(sentences),
            "section_ids": sorted(section_ids),
            "parent_ids_prev": parent_ids_prev,
            "speaker_majority": speaker_majority,
            "speaker_switches": speaker_switches,
            "speakers": speaker_histogram,
            "text_human": chunk_text,
            "text_machine": chunk_text_machine,
            "overlap_sentences": self.overlap_sentences,
            "confidence_mean": confidence_mean,
            "confidence_p05": confidence_p05,
            "low_span_ratio": low_span_ratio,
            "sentences": [
                {
                    "text": sentence.get("text"),
                    "start": sentence.get("start"),
                    "end": sentence.get("end"),
                    "speaker": sentence.get("speaker"),
                    "tokens": sentence.get("tokens"),
                    "overlap": bool(sentence.get("overlap")),
                    "section_id": sentence.get("section_id"),
                    "confidence_mean": sentence.get("confidence_mean"),
                    "confidence_p05": sentence.get("confidence_p05"),
                    "low_duration": sentence.get("low_duration"),
                }
                for sentence in sentences
            ],
        }

    def _estimate_tokens(self, text: Optional[str]) -> int:
        if not text:
            return 1
        words = len(text.split())
        return max(1, int(math.ceil(words * 1.3)))

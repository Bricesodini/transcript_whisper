import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils import sanitize_whisper_text


class SegmentRefiner:
    def __init__(self, config: Dict, logger, asr_processor):
        self.logger = logger
        self.asr = asr_processor
        self.cfg = config.get("refine", {})
        self.enabled = bool(self.cfg.get("enabled", False))
        self.low_conf_threshold = float(self.cfg.get("low_conf_threshold", 0.5))
        self.min_low_conf_ratio = float(self.cfg.get("min_low_conf_ratio", 0.1))
        self.padding = float(self.cfg.get("padding", 0.2))
        self.max_segment_duration = float(self.cfg.get("max_segment_duration", 25.0))
        self.clip_sample_rate = int(self.cfg.get("clip_sample_rate", 16000))
        self.cleanup_clips = bool(self.cfg.get("cleanup_clips", True))
        self.window_tolerance = float(self.cfg.get("window_tolerance", 0.15))
        self.override_beam_size = self.cfg.get("beam_size")
        self.override_vad_filter = self.cfg.get("vad_filter", False)
        self.override_temperature = self.cfg.get("temperature")

    def run(
        self,
        audio_path: Path,
        segments: List[Dict[str, Any]],
        language: str,
        build_dir: Path,
    ) -> List[Dict[str, Any]]:
        if not self.enabled or not segments:
            return segments
        targets = self._segments_to_refine(segments)
        if not targets:
            self.logger.info("Re-ASR: aucun segment douteux (ratio < %.0f%%)", self.min_low_conf_ratio * 100)
            return segments
        refined: List[Dict[str, Any]] = []
        target_set = set(targets)
        self.logger.info("Re-ASR: %d segments marqués pour ré-analyse locale", len(targets))
        for idx, seg in enumerate(segments):
            if idx in target_set:
                updated = self._refine_segment(idx, seg, audio_path, language, build_dir)
                refined.append(updated or seg)
            else:
                refined.append(seg)
        return refined

    def _segments_to_refine(self, segments: List[Dict[str, Any]]) -> List[int]:
        indices: List[int] = []
        for idx, seg in enumerate(segments):
            duration = float(seg.get("end", 0.0)) - float(seg.get("start", 0.0))
            if duration <= 0 or duration > self.max_segment_duration:
                continue
            words = seg.get("words") or []
            if not words:
                continue
            low = sum(1 for word in words if self._is_low_conf(word))
            if not low:
                continue
            ratio = low / max(len(words), 1)
            if ratio >= self.min_low_conf_ratio:
                indices.append(idx)
        return indices

    def _is_low_conf(self, word: Dict[str, Any]) -> bool:
        probability = word.get("probability")
        if probability is None:
            return False
        try:
            value = float(probability)
        except (TypeError, ValueError):
            return False
        return value < self.low_conf_threshold

    def _refine_segment(
        self,
        index: int,
        segment: Dict[str, Any],
        audio_path: Path,
        language: str,
        build_dir: Path,
    ) -> Optional[Dict[str, Any]]:
        clip_start = max(0.0, float(segment["start"]) - self.padding)
        clip_end = float(segment["end"]) + self.padding
        clip_duration = clip_end - clip_start
        if clip_duration <= 0 or clip_duration > self.max_segment_duration + (self.padding * 2):
            return None
        clip_dir = build_dir / "refine"
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip_path = clip_dir / f"segment_{index:04d}_{uuid.uuid4().hex}.wav"
        if not self._extract_clip(audio_path, clip_path, clip_start, clip_end):
            return None
        text, words = self._transcribe_clip(clip_path, language)
        if self.cleanup_clips:
            try:
                clip_path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
        if not text and not words:
            return None
        shifted = self._shift_words(words, clip_start)
        windowed = self._filter_words(shifted, float(segment["start"]), float(segment["end"]))
        updated = dict(segment)
        if text:
            updated["text"] = text
        if windowed:
            updated["words"] = windowed
        return updated

    def _extract_clip(self, audio_path: Path, clip_path: Path, start: float, end: float) -> bool:
        if end <= start:
            return False
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start:.2f}",
            "-to",
            f"{end:.2f}",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            str(self.clip_sample_rate),
            str(clip_path),
        ]
        try:
            subprocess.run(command, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            self.logger.warning("Re-ASR: extraction audio impossible (%s)", exc)
            return False

    def _transcribe_clip(self, clip_path: Path, language: str) -> Tuple[str, List[Dict[str, Any]]]:
        try:
            model = self.asr.load_model()
        except Exception as exc:  # pragma: no cover
            self.logger.warning("Re-ASR: modèle indisponible (%s)", exc)
            return "", []
        asr_cfg = getattr(self.asr, "asr_cfg", {})
        beam_size = self.override_beam_size or asr_cfg.get("beam_size", 5)
        temperature = (
            self.override_temperature
            if self.override_temperature is not None
            else asr_cfg.get("temperature", 0.0)
        )
        vad_filter = self.override_vad_filter if self.override_vad_filter is not None else asr_cfg.get("vad_filter", True)
        text_parts: List[str] = []
        words: List[Dict[str, Any]] = []
        segments_iter, _ = model.transcribe(
            str(clip_path),
            beam_size=int(beam_size),
            temperature=float(temperature),
            language=None if language == "auto" else language,
            vad_filter=bool(vad_filter),
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        for seg in segments_iter:
            cleaned = sanitize_whisper_text(seg.text)
            if cleaned:
                text_parts.append(cleaned.strip())
            for w in seg.words or []:
                words.append(
                    {
                        "start": float(w.start or 0.0),
                        "end": float(w.end or 0.0),
                        "word": sanitize_whisper_text(w.word),
                        "probability": getattr(w, "probability", 1.0),
                    }
                )
        return " ".join(part for part in text_parts if part).strip(), words

    def _shift_words(self, words: List[Dict[str, Any]], offset: float) -> List[Dict[str, Any]]:
        shifted: List[Dict[str, Any]] = []
        for word in words:
            new_word = dict(word)
            new_word["start"] = offset + float(word.get("start", 0.0))
            new_word["end"] = offset + float(word.get("end", 0.0))
            shifted.append(new_word)
        return shifted

    def _filter_words(self, words: List[Dict[str, Any]], start: float, end: float) -> List[Dict[str, Any]]:
        if not words:
            return []
        tolerance = self.window_tolerance
        filtered = []
        for word in words:
            w_start = float(word.get("start", 0.0))
            w_end = float(word.get("end", 0.0))
            if (w_end >= start - tolerance) and (w_start <= end + tolerance):
                filtered.append(word)
        return filtered

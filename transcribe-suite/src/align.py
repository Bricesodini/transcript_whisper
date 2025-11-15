import os
from pathlib import Path
from typing import Dict, List

import torch

from utils import (
    PipelineError,
    compute_post_threads,
    configure_torch_threads,
    read_json,
    sanitize_whisper_text,
    write_json,
)

try:
    import whisperx
except ImportError as exc:  # pragma: no cover
    whisperx = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class Aligner:
    def __init__(self, config: Dict, logger):
        self.logger = logger
        self.cfg = config.get("align", {})
        requested_device = self.cfg.get("device", config.get("asr", {}).get("device", "auto"))
        self.device = self._resolve_device(requested_device)
        self._cache = {}
        self.max_workers = max(1, int(self.cfg.get("workers", 4) or 4))
        self.batch_size = max(0, int(self.cfg.get("batch_size", 0) or 0))
        self.speech_only = bool(self.cfg.get("speech_only", False))

    def prewarm(self, language: str) -> None:
        lang = language or "en"
        try:
            self._load_align_model(lang)
        except PipelineError:
            raise

    def _load_align_model(self, language: str):
        if whisperx is None:
            raise PipelineError(f"whisperx non disponible: {IMPORT_ERROR}")
        lang = language or "en"
        if lang in self._cache:
            return self._cache[lang]
        self.logger.info("Chargement du modèle d'alignement WhisperX (%s)", lang)
        model_name = self.cfg.get("model_name")
        align_model, metadata = whisperx.load_align_model(
            language_code=lang,
            device=self.device,
            model_name=model_name,
        )
        self._cache[lang] = (align_model, metadata)
        return align_model, metadata

    def _assign_speakers(self, segments: List[Dict], diar_segments: List[Dict]) -> None:
        if not diar_segments:
            return

        def find_speaker(ts: float) -> str:
            for segment in diar_segments:
                if segment["start"] <= ts <= segment["end"]:
                    return segment["speaker"]
            return diar_segments[-1]["speaker"]

        for seg in segments:
            midpoint = (seg["start"] + seg["end"]) / 2
            speaker = find_speaker(midpoint)
            seg["speaker"] = speaker
            for word in seg.get("words", []):
                w_mid = (word["start"] + word["end"]) / 2
                word["speaker"] = find_speaker(w_mid)

    def run(
        self,
        audio_path: Path,
        asr_result: Dict,
        diarization_result: Dict,
        work_dir: Path,
        force: bool = False,
    ) -> Dict:
        configure_torch_threads(self._thread_budget(), interop_threads=2)
        language = asr_result.get("language") or "en"
        aligned_path = work_dir / "03_aligned_whisperx.json"
        if aligned_path.exists() and not force:
            payload = read_json(aligned_path)
            self.logger.info("Alignement WhisperX (cache) ➜ %s", aligned_path)
            return {
                "segments": payload.get("segments", []),
                "language": payload.get("language", language),
                "path": aligned_path,
            }
        align_model, metadata = self._load_align_model(language)
        diar_segments = diarization_result.get("segments", []) if diarization_result else []
        source_segments = asr_result.get("segments", [])
        segments_for_align = self._filter_speech_segments(source_segments, diar_segments)
        if segments_for_align:
            self.logger.info("🔍 AVANT WhisperX align: %s", repr(segments_for_align[0].get("text", "")[:80]))
        align_kwargs = {"device": self.device}
        if self.max_workers:
            align_kwargs["num_workers"] = self.max_workers
        if self.batch_size:
            align_kwargs["batch_size"] = self.batch_size
        aligned = self._invoke_align(segments_for_align, align_model, metadata, audio_path, align_kwargs)
        aligned_segments = aligned.get("segments", [])
        if aligned_segments:
            self.logger.info("🔍 APRÈS WhisperX align: %s", repr(aligned_segments[0].get("text", "")[:80]))
        for segment in aligned.get("segments", []):
            if "text" in segment:
                self.logger.info("🔍 Avant sanitize align: %s", repr(segment["text"][:80]))
                segment["text"] = sanitize_whisper_text(segment["text"])
                self.logger.info("✅ Après sanitize align: %s", repr(segment["text"][:80]))
            for word in segment.get("words", []):
                if "word" in word:
                    word["word"] = sanitize_whisper_text(word["word"])
        self._assign_speakers(aligned.get("segments", []), diar_segments)
        aligned.setdefault("language", language)
        aligned_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(aligned_path, aligned)
        log_path = work_dir / "logs" / "align.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as handle:
            handle.write(f"Alignement WhisperX OK ({len(aligned_segments)} segments)\n")
        return {"segments": aligned.get("segments", []), "language": language, "path": aligned_path}

    def _resolve_device(self, requested: str) -> str:
        req = (requested or "auto").lower()
        if req == "auto":
            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
            return "cpu"
        if req == "metal":
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps"
            self.logger.warning("Device 'metal' indisponible, repli sur CPU.")
            return "cpu"
        return req

    def _thread_budget(self) -> int:
        value = os.environ.get("POST_THREADS")
        try:
            parsed = int(value) if value is not None else 0
        except ValueError:
            parsed = 0
        return max(4, parsed or compute_post_threads())

    def _filter_speech_segments(self, segments: List[Dict], diar_segments: List[Dict]) -> List[Dict]:
        if not self.speech_only or not diar_segments:
            return segments
        windows = []
        for seg in diar_segments:
            try:
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", start))
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            windows.append((start, end))
        if not windows:
            return segments
        filtered: List[Dict] = []
        for seg in segments:
            try:
                start = float(seg.get("start", 0.0))
                end = float(seg.get("end", start))
            except (TypeError, ValueError):
                filtered.append(seg)
                continue
            midpoint = (start + end) / 2
            if any(win_start - 0.05 <= midpoint <= win_end + 0.05 for win_start, win_end in windows):
                filtered.append(seg)
        if not filtered:
            self.logger.warning("speech-only actif mais aucune fenêtre overlap ➜ fallback segments complets")
            return segments
        drop_count = len(segments) - len(filtered)
        if drop_count:
            self.logger.info("speech-only: %d/%d segments ignorés avant align", drop_count, len(segments))
        return filtered

    def _invoke_align(self, segments, align_model, metadata, audio_path, kwargs):
        try:
            return whisperx.align(
                segments,
                align_model,
                metadata,
                str(audio_path),
                **kwargs,
            )
        except TypeError as exc:
            lowered = str(exc).lower()
            for key in ("num_workers", "batch_size"):
                needle = key.replace("_", " ")
                if key in kwargs and (needle in lowered or key in lowered):
                    self.logger.warning("Option WhisperX '%s' non supportée ➜ ignorée", key)
                    kwargs.pop(key, None)
                    return self._invoke_align(segments, align_model, metadata, audio_path, kwargs)
            raise

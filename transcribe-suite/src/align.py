import inspect
import os
from pathlib import Path
from typing import Dict, List

from utils import (
    PipelineError,
    compute_post_threads,
    configure_torch_threads,
    resolve_runtime_device,
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
        self.device = resolve_runtime_device(requested_device, logger=self.logger, label="Align")
        self._cache = {}
        self.max_workers = max(1, int(self.cfg.get("workers", 4) or 4))
        self.batch_size = max(0, int(self.cfg.get("batch_size", 0) or 0))
        self.speech_only = bool(self.cfg.get("speech_only", False))
        self._align_params_cache = None
        self._align_callable = None

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
            self.logger.info("Alignement WhisperX (cache) -> %s", aligned_path)
            return {
                "segments": payload.get("segments", []),
                "language": payload.get("language", language),
                "path": aligned_path,
                "align_status": payload.get("align_status", "cache"),
            }

        align_model, metadata = self._load_align_model(language)
        diar_segments = diarization_result.get("segments", []) if diarization_result else []
        source_segments = asr_result.get("segments", [])
        segments_for_align = self._filter_speech_segments(source_segments, diar_segments)
        if segments_for_align:
            self.logger.info("AVANT WhisperX align: %s", repr(segments_for_align[0].get("text", "")[:80]))

        requested_kwargs = {"device": self.device}
        if self.max_workers:
            requested_kwargs["num_workers"] = self.max_workers
        if self.batch_size:
            requested_kwargs["batch_size"] = self.batch_size
        align_kwargs, dropped_kwargs = self._filter_align_kwargs(requested_kwargs)
        if dropped_kwargs:
            self.logger.info("Kwargs WhisperX non supportes ignores: %s", ", ".join(dropped_kwargs))

        align_error = None
        align_status = "ok"
        try:
            aligned = self._invoke_align(segments_for_align, align_model, metadata, audio_path, align_kwargs)
        except Exception as exc:  # pragma: no cover
            self.logger.warning(
                "WhisperX align a echoue (%s). Fallback: segments non alignes mot-a-mot.",
                exc,
            )
            aligned = {
                "segments": segments_for_align,
                "language": language,
            }
            align_status = "skipped"
            align_error = f"{exc.__class__.__name__}: {str(exc)[:200]}"

        aligned["align_status"] = align_status
        if align_error:
            aligned["align_error"] = align_error
        if dropped_kwargs:
            aligned["align_filtered_kwargs"] = dropped_kwargs
        aligned["align_device"] = self.device
        if align_status == "skipped":
            aligned.setdefault("reason", align_error or "whisperx_failed")

        aligned_segments = aligned.get("segments", [])
        if align_status == "ok" and aligned_segments:
            self.logger.info("APRES WhisperX align: %s", repr(aligned_segments[0].get("text", "")[:80]))

        for segment in aligned_segments:
            if "text" in segment:
                self.logger.info("Avant sanitize align: %s", repr(segment["text"][:80]))
                segment["text"] = sanitize_whisper_text(segment["text"])
                self.logger.info("Apres sanitize align: %s", repr(segment["text"][:80]))
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
            message = f"Alignement WhisperX {align_status.upper()} ({len(aligned_segments)} segments)"
            if align_error:
                message += f" - {align_error}"
            handle.write(message + "\n")

        result = {
            "segments": aligned.get("segments", []),
            "language": language,
            "path": aligned_path,
            "align_status": align_status,
        }
        if align_error:
            result["align_error"] = align_error
        if dropped_kwargs:
            result["align_filtered_kwargs"] = dropped_kwargs
        return result

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
        align_fn = self._get_align_callable()
        return align_fn(
            segments,
            align_model,
            metadata,
            str(audio_path),
            **kwargs,
        )

    def _filter_align_kwargs(self, kwargs: Dict):
        allowed = self._allowed_align_params()
        if not allowed:
            return kwargs, []
        filtered = {}
        dropped = []
        for key, value in kwargs.items():
            if key in allowed:
                filtered[key] = value
            else:
                dropped.append(key)
        return filtered, dropped

    def _allowed_align_params(self) -> set:
        if self._align_params_cache is not None:
            return self._align_params_cache
        allowed = set()
        if whisperx is None:
            self._align_params_cache = allowed
            return allowed
        try:
            align_fn = self._get_align_callable()
            sig = inspect.signature(align_fn)
            allowed = set(sig.parameters.keys())
        except Exception:
            allowed = set()
        self._align_params_cache = allowed
        return allowed

    def _get_align_callable(self):
        if self._align_callable is not None:
            return self._align_callable
        if whisperx is None:
            raise PipelineError("whisperx indisponible pour l'alignement")
        func = None
        align_module = getattr(whisperx, 'alignment', None)
        if align_module is not None and hasattr(align_module, 'align'):
            func = getattr(align_module, 'align')
        elif hasattr(whisperx, 'align'):
            func = whisperx.align
        if func is None:
            raise PipelineError("Version whisperx incompatible: fonction align introuvable")
        self._align_callable = func
        return func

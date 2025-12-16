import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from utils import PipelineError, compute_post_threads, configure_torch_threads

try:
    from pyannote.audio import Pipeline as PyannotePipeline
except ImportError as exc:  # pragma: no cover
    PyannotePipeline = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None

try:
    import torch
except ImportError:  # pragma: no cover
    torch = None


SAFE_GLOBALS = []

try:
    from torch.torch_version import TorchVersion  # type: ignore
except Exception:
    TorchVersion = None
else:
    SAFE_GLOBALS.append(TorchVersion)

try:
    from pyannote.audio.core.task import Specifications, Problem, Resolution  # type: ignore
except Exception:
    Specifications = None
    Problem = None
    Resolution = None
else:
    SAFE_GLOBALS.extend([Specifications, Problem, Resolution])


class Diarizer:
    def __init__(self, config: Dict, logger):
        self.logger = logger
        self.cfg = config.get("diarization", {})
        self._pipeline: Optional[PyannotePipeline] = None
        self.device = self.cfg.get("device", "cpu")
        self.seg_batch = int(self.cfg.get("segmentation_batch", 0) or 0)
        self.emb_batch = int(self.cfg.get("embedding_batch", 0) or 0)
        self.speech_mask_enabled = bool(self.cfg.get("speech_mask", False))
        self.speech_mask_margin = float(self.cfg.get("speech_mask_margin", 0.05) or 0.05)
        self.pipeline_params = self.cfg.get("pipeline_params") or {}

    def load(self):
        if self._pipeline is not None:
            return self._pipeline
        if PyannotePipeline is None:
            raise PipelineError(f"pyannote.audio manquant: {IMPORT_ERROR}")
        model_id = self.cfg.get("model", "pyannote/speaker-diarization")
        token_env = self.cfg.get("authorization_env", "PYANNOTE_TOKEN")
        token = self.cfg.get("authorization_token") or self.cfg.get("token")
        if not token:
            token = token_env and (self.cfg.get(token_env) or os.environ.get(token_env))
        if not token:
            raise PipelineError(
                "Pyannote nécessite un token HuggingFace. "
                f"Définis la variable d'environnement {token_env}."
            )
        self.logger.info("Chargement du pipeline Pyannote (%s)", model_id)
        if torch is not None and SAFE_GLOBALS:
            try:
                import torch.serialization as ts

                if hasattr(ts, "add_safe_globals"):
                    ts.add_safe_globals([cls for cls in SAFE_GLOBALS if cls])
                    self.logger.info(
                        "PyTorch safe_globals activés pour %s",
                        ", ".join(cls.__name__ for cls in SAFE_GLOBALS if cls),
                    )
            except Exception as exc:  # pragma: no cover
                self.logger.warning(
                    "Impossible d'activer les safe_globals Pyannote (%s). "
                    "Pour tout nouvel 'Unsupported global', ajoutez la classe concernée ici.",
                    exc,
                )
        pipeline = PyannotePipeline.from_pretrained(model_id, use_auth_token=token)
        device_obj = None
        final_label = "cpu"
        if torch is not None:
            if torch.cuda.is_available():
                device_obj = torch.device("cuda")
            else:
                hint = (self.device or "cpu")
                try:
                    device_obj = torch.device(hint)
                except Exception:
                    self.logger.warning("Device Pyannote invalide '%s' ➜ cpu", hint)
                    device_obj = torch.device("cpu")
        if device_obj is not None:
            try:
                pipeline.to(device_obj)
                final_label = device_obj.type
            except AttributeError:
                self.logger.debug("Pipeline Pyannote sans support explicite .to(); device par défaut conservé.")
                final_label = getattr(device_obj, "type", str(device_obj))
            except Exception as exc:  # pragma: no cover
                self.logger.warning(
                    "Impossible de basculer Pyannote sur %s (%s) ➜ cpu",
                    getattr(device_obj, "type", device_obj),
                    exc,
                )
                if torch is not None:
                    try:
                        pipeline.to(torch.device("cpu"))
                    except Exception as fallback_exc:  # pragma: no cover
                        self.logger.warning("Fallback Pyannote sur cpu impossible (%s)", fallback_exc)
                final_label = "cpu"
        self.logger.info("Pyannote ➜ device %s", final_label)
        self._configure_batches(pipeline)
        self._apply_pipeline_params(pipeline)
        self._pipeline = pipeline
        return self._pipeline

    def run(self, audio_path: Path, build_dir: Path, speech_segments: Optional[List[Dict]] = None) -> Dict:
        configure_torch_threads(self._thread_budget(), interop_threads=2)
        pipeline = self.load()
        diarization = pipeline(str(audio_path))
        diarization.uri = self._safe_uri(audio_path.stem)
        rttm_path = build_dir / "diarization.rttm"
        rttm_path.parent.mkdir(parents=True, exist_ok=True)
        with rttm_path.open("w", encoding="utf-8") as handle:
            diarization.write_rttm(handle)
        segments: List[Dict] = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            segments.append(
                {
                    "start": round(float(turn.start), 3),
                    "end": round(float(turn.end), 3),
                    "speaker": speaker,
                }
            )
        segments = self._stabilize_segments(segments)
        if self.speech_mask_enabled and speech_segments:
            segments = self._apply_speech_mask(segments, speech_segments)
        max_speakers = int(self.cfg.get("max_speakers", 0) or 0)
        if max_speakers > 0:
            segments = self._limit_speakers(segments, max_speakers)
        return {"segments": segments, "rttm": rttm_path}

    def _configure_batches(self, pipeline: PyannotePipeline) -> None:
        if self.seg_batch and hasattr(pipeline, "segmentation"):
            try:
                pipeline.segmentation.batch_size = int(self.seg_batch)
            except Exception:  # pragma: no cover
                self.logger.warning("Batch segmentation=%s non appliqué", self.seg_batch)
        if self.emb_batch and hasattr(pipeline, "embedding"):
            try:
                pipeline.embedding.batch_size = int(self.emb_batch)
            except Exception:  # pragma: no cover
                self.logger.warning("Batch embedding=%s non appliqué", self.emb_batch)

    def _apply_pipeline_params(self, pipeline: PyannotePipeline) -> None:
        if not self.pipeline_params:
            return
        try:
            pipeline.instantiate(self.pipeline_params)
            self.logger.info(
                "Paramètres Pyannote personnalisés appliqués (%s)",
                ", ".join(self.pipeline_params.keys()),
            )
        except Exception as exc:  # pragma: no cover
            self.logger.warning("Impossible d'appliquer les paramètres Pyannote (%s)", exc)

    def _safe_uri(self, stem: str) -> str:
        """Pyannote RTTM writer rejects URIs with spaces => sanitize."""
        clean = re.sub(r"\s+", "_", stem.strip())
        clean = re.sub(r"[^\w.\-]+", "_", clean)
        return clean or "audio"

    def _stabilize_segments(self, segments: List[Dict]) -> List[Dict]:
        if not segments:
            return []
        ordered = sorted(segments, key=lambda seg: (seg.get("start", 0.0), seg.get("end", 0.0)))
        speakers = {seg.get("speaker") for seg in ordered if seg.get("speaker")}
        merge_single = self.cfg.get("merge_single_speaker", True)
        if merge_single and len(speakers) <= 1:
            merged = {
                "start": ordered[0]["start"],
                "end": ordered[-1]["end"],
                "speaker": next(iter(speakers), "SPEAKER_00"),
            }
            return [merged]
        min_turn = float(self.cfg.get("min_speaker_turn", 1.2))
        if min_turn <= 0:
            return ordered
        stabilized: List[Dict] = [dict(ordered[0])]
        for seg in ordered[1:]:
            last = stabilized[-1]
            if seg.get("speaker") == last.get("speaker"):
                last["end"] = max(last["end"], seg["end"])
                continue
            duration = float(seg["end"]) - float(seg["start"])
            if duration < min_turn:
                last["end"] = max(last["end"], seg["end"])
                continue
            stabilized.append(dict(seg))
        return stabilized

    def _limit_speakers(self, segments: List[Dict], max_speakers: int) -> List[Dict]:
        if not segments or max_speakers <= 0:
            return segments
        template = "SPEAKER_{:02d}"
        speaker_map: Dict[str, str] = {}
        for seg in segments:
            speaker = seg.get("speaker") or "unknown"
            if speaker in speaker_map:
                seg["speaker"] = speaker_map[speaker]
                continue
            if len(speaker_map) < max_speakers:
                label = template.format(len(speaker_map))
                speaker_map[speaker] = label
                seg["speaker"] = label
            else:
                seg["speaker"] = template.format(max_speakers - 1)
        return segments

    def _apply_speech_mask(self, segments: List[Dict], speech_segments: List[Dict]) -> List[Dict]:
        windows = []
        for seg in speech_segments:
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
        masked: List[Dict] = []
        margin = max(0.0, self.speech_mask_margin)
        for seg in segments:
            mid = (seg.get("start", 0.0) + seg.get("end", seg.get("start", 0.0))) / 2
            if any(win_start - margin <= mid <= win_end + margin for win_start, win_end in windows):
                masked.append(seg)
        if not masked:
            self.logger.warning("Speech-mask n'a conservé aucun segment diar ➜ fallback complet")
            return segments
        self.logger.info("Speech-mask: %d/%d segments conservés", len(masked), len(segments))
        return masked

    def _thread_budget(self) -> int:
        value = os.environ.get("POST_THREADS")
        try:
            parsed = int(value) if value is not None else 0
        except ValueError:
            parsed = 0
        return max(4, parsed or compute_post_threads())

    def _sanitize_device(self, hint: Optional[str]):
        if torch is None:
            return None
        if hint is None or str(hint).strip().lower() in {"", "auto"}:
            return torch.device("cpu")
        try:
            return torch.device(hint)
        except Exception:
            self.logger.warning("Device Pyannote invalide '%s' ➜ cpu", hint)
            return torch.device("cpu")

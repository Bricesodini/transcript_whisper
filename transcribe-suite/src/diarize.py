import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from utils import PipelineError

try:
    from pyannote.audio import Pipeline as PyannotePipeline
except ImportError as exc:  # pragma: no cover
    PyannotePipeline = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class Diarizer:
    def __init__(self, config: Dict, logger):
        self.logger = logger
        self.cfg = config.get("diarization", {})
        self._pipeline: Optional[PyannotePipeline] = None

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
        self._pipeline = PyannotePipeline.from_pretrained(model_id, use_auth_token=token)
        return self._pipeline

    def run(self, audio_path: Path, build_dir: Path) -> Dict:
        pipeline = self.load()
        diarization = pipeline(str(audio_path))
        diarization.uri = self._safe_uri(audio_path.stem)
        rttm_path = build_dir / "diarization.rttm"
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
        max_speakers = int(self.cfg.get("max_speakers", 0) or 0)
        if max_speakers > 0:
            segments = self._limit_speakers(segments, max_speakers)
        return {"segments": segments, "rttm": rttm_path}

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

import csv
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from utils import PipelineError, write_json


class Segmenter:
    def __init__(self, config: Dict, logger):
        self.logger = logger
        self.cfg = config.get("segmenter", {})
        self.segment_length = float(self.cfg.get("segment_length", 75.0))
        self.overlap = float(self.cfg.get("overlap", 8.0))
        self.sample_rate = int(self.cfg.get("sample_rate", 16000))
        self.channels = int(self.cfg.get("channels", 1))
        self.manifest_name = self.cfg.get("manifest_name", "manifest.csv")
        if self.segment_length <= self.overlap:
            raise PipelineError("segment_length doit être strictement supérieur à overlap")

    def run(self, audio_path: Path, work_dir: Path, force: bool = False) -> Dict[str, Path]:
        if not audio_path.exists():
            raise PipelineError(f"Audio introuvable pour segmentation: {audio_path}")
        work_dir.mkdir(parents=True, exist_ok=True)
        segments_dir = work_dir / "00_segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = work_dir / self.manifest_name
        state_path = work_dir / "manifest_state.json"

        if manifest_path.exists() and not force:
            self.logger.info("Manifest existant, reuse segmentation ➜ %s", manifest_path)
            if not state_path.exists():
                self._initialize_state(manifest_path, state_path)
            return {
                "manifest": manifest_path,
                "segments_dir": segments_dir,
                "state_path": state_path,
            }

        duration = self._probe_duration(audio_path)
        if duration <= 0:
            raise PipelineError("Durée audio invalide (<= 0)")

        segment_length_ms = int(round(self.segment_length * 1000))
        overlap_ms = int(round(self.overlap * 1000))
        hop_ms = max(segment_length_ms - overlap_ms, 1)
        total_ms = int(round(duration * 1000))

        records: List[Dict[str, str]] = []
        idx = 0
        start_ms = 0
        while start_ms < total_ms:
            end_ms = min(start_ms + segment_length_ms, total_ms)
            seg_name = f"seg_{idx:05d}__from_{start_ms}__to_{end_ms}.wav"
            seg_path = segments_dir / seg_name
            self._slice_audio(audio_path, seg_path, start_ms, end_ms)
            rel_path = Path("00_segments") / seg_name
            records.append(
                {
                    "index": idx,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "path": str(rel_path),
                    "status": "PENDING",
                }
            )
            idx += 1
            start_ms += hop_ms

        self._write_manifest(manifest_path, records)
        self._initialize_state(manifest_path, state_path)
        self.logger.info("Segmentation: %d fenêtres de %ss (+%ss) pour %.2f min", idx, self.segment_length, self.overlap, duration / 60)
        return {
            "manifest": manifest_path,
            "segments_dir": segments_dir,
            "state_path": state_path,
        }

    def _probe_duration(self, audio_path: Path) -> float:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise PipelineError(f"Impossible de lire la durée audio ({exc})") from exc
        try:
            return float(result.stdout.strip())
        except ValueError as exc:  # pragma: no cover
            raise PipelineError(f"Durée audio illisible: {result.stdout}") from exc

    def _slice_audio(self, audio_path: Path, segment_path: Path, start_ms: int, end_ms: int) -> None:
        start_sec = start_ms / 1000.0
        end_sec = end_ms / 1000.0
        duration = end_sec - start_sec
        if duration <= 0:
            raise PipelineError("Durée de segment négative")
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_sec:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(audio_path),
            "-ac",
            str(self.channels),
            "-ar",
            str(self.sample_rate),
            str(segment_path),
        ]
        try:
            subprocess.run(cmd, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            raise PipelineError(f"Découpage segment échoué ({segment_path}): {exc}") from exc

    def _write_manifest(self, manifest_path: Path, rows: List[Dict]) -> None:
        with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
            writer = csv.DictWriter(handle, fieldnames=["index", "start_ms", "end_ms", "path", "status"])
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def _initialize_state(self, manifest_path: Path, state_path: Path) -> None:
        with manifest_path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            segments = {
                str(int(row["index"])): {
                    "status": row.get("status", "PENDING"),
                    "retries": 0,
                }
                for row in reader
            }
        payload = {
            "meta": {
                "created_at": datetime.utcnow().isoformat() + "Z",
                "segment_length": self.segment_length,
                "overlap": self.overlap,
            },
            "segments": segments,
        }
        write_json(state_path, payload, indent=2)

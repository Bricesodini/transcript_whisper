from pathlib import Path
from typing import Dict

from utils import PipelineError, run_cmd


class Preprocessor:
    def __init__(self, config: Dict, logger):
        self.cfg = config.get("preproc", {})
        self.logger = logger

    def run(self, media_path: Path, work_dir: Path, force: bool = False) -> Path:
        if not media_path.exists():
            raise PipelineError(f"Input media missing: {media_path}")

        target_sr = str(self.cfg.get("target_sr", 16000))
        channels = str(self.cfg.get("channels", 1))
        loudnorm = self.cfg.get("loudnorm", True)
        vad_cfg: Dict = self.cfg.get("vad", {})
        silence_duration = str(vad_cfg.get("silence_duration", 0.5))
        silence_threshold = str(vad_cfg.get("silence_threshold", -40))
        denoise = self.cfg.get("denoise")

        work_dir.mkdir(parents=True, exist_ok=True)
        output = work_dir / "audio_16k.wav"
        if output.exists() and not force:
            self.logger.info("Audio 16 kHz déjà présent ➜ %s", output)
            return output
        filters = []
        if loudnorm:
            filters.append("loudnorm=I=-16:LRA=11:TP=-1.5")

        denoise_filter = self._denoise_filter(denoise)
        if denoise_filter:
            filters.append(denoise_filter)

        if vad_cfg.get("enabled", True):
            filters.append(
                "silenceremove="
                f"start_periods=1:start_duration={silence_duration}:start_threshold={silence_threshold}dB:"
                f"stop_periods=-1:stop_duration={silence_duration}:stop_threshold={silence_threshold}dB"
            )

        filter_chain = ",".join(filters) if filters else "anull"

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(media_path),
            "-ac",
            channels,
            "-ar",
            target_sr,
            "-map_metadata",
            "-1",
            "-vn",
            "-af",
            filter_chain,
        ]

        cmd += [str(output)]
        run_cmd(cmd, self.logger)
        return output

    def _denoise_filter(self, denoise_cfg):
        if not denoise_cfg:
            return None
        mode = "light"
        if isinstance(denoise_cfg, str):
            mode = denoise_cfg.lower()
        elif isinstance(denoise_cfg, bool):
            mode = "light" if denoise_cfg else None
        if mode is None:
            return None
        if mode == "aggressive":
            return "afftdn=nf=-35:tn=1"
        if mode == "medium":
            return "afftdn=nf=-30:tn=1"
        return "afftdn=nf=-25:tn=1"

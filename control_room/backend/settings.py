from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional


def _as_path(value: Optional[str], fallback: Path) -> Path:
    if not value:
        return fallback
    return Path(value).expanduser()


@dataclass
class Settings:
    data_pipeline_root: Path
    ts_repo_root: Path
    ts_run_bat_path: Optional[Path]
    ts_venv_dir: Optional[Path]
    jobs_db_path: Path
    logs_dir: Path
    frontend_dist_dir: Path
    api_key: Optional[str]
    max_workers: int
    job_version: int
    preview_timeout_ms: int
    profiles_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        repo_default = Path(__file__).resolve().parents[2]
        data_default = Path(r"\\bricesodini\Savoirs\03_data_pipeline")
        data_root = _as_path(os.getenv("DATA_PIPELINE_ROOT"), data_default)
        ts_repo = _as_path(os.getenv("TS_REPO_ROOT"), repo_default)
        run_bat = os.getenv("TS_RUN_BAT_PATH")
        ts_run_bat_path = Path(run_bat) if run_bat else ts_repo / "bin" / "run.bat"
        ts_venv = os.getenv("TS_VENV_DIR")
        jobs_db = _as_path(os.getenv("CONTROL_ROOM_JOBS_DB"), ts_repo / "control_room" / "backend" / "jobs.db")
        logs_dir = _as_path(os.getenv("CONTROL_ROOM_LOG_DIR"), ts_repo / "control_room" / "backend" / "job_logs")
        frontend_dist = _as_path(os.getenv("CONTROL_ROOM_FRONTEND_DIST"), ts_repo / "control_room" / "frontend" / "dist")
        max_workers = int(os.getenv("CONTROL_ROOM_MAX_WORKERS", "2"))
        preview_timeout = int(os.getenv("CONTROL_ROOM_PREVIEW_TIMEOUT_MS", "500"))
        api_key = os.getenv("CONTROL_ROOM_API_KEY")
        profiles_path = _as_path(os.getenv("CONTROL_ROOM_PROFILES"), ts_repo / "control_room" / "profiles.yaml")
        return cls(
            data_pipeline_root=data_root,
            ts_repo_root=ts_repo,
            ts_run_bat_path=ts_run_bat_path,
            ts_venv_dir=Path(ts_venv).expanduser() if ts_venv else None,
            jobs_db_path=jobs_db,
            logs_dir=logs_dir,
            frontend_dist_dir=frontend_dist,
            api_key=api_key,
            max_workers=max_workers,
            job_version=1,
            preview_timeout_ms=preview_timeout,
            profiles_path=profiles_path,
        )

    @property
    def input_audio_dir(self) -> Path:
        return self.data_pipeline_root / "01_input" / "audio"

    @property
    def input_video_dir(self) -> Path:
        return self.data_pipeline_root / "01_input" / "video"

    @property
    def asr_staging_dir(self) -> Path:
        return self.data_pipeline_root / "02_output_source" / "asr"

    @property
    def rag_output_dir(self) -> Path:
        return self.data_pipeline_root / "03_output_RAG"

    @property
    def bin_dir(self) -> Path:
        return self.ts_repo_root / "bin"

    @property
    def run_bat_path(self) -> Path:
        return self.ts_run_bat_path or (self.bin_dir / "run.bat")


@lru_cache()
def get_settings() -> Settings:
    settings = Settings.from_env()
    settings.data_pipeline_root = settings.data_pipeline_root.resolve()
    settings.ts_repo_root = settings.ts_repo_root.resolve()
    settings.jobs_db_path = settings.jobs_db_path.resolve()
    settings.logs_dir = settings.logs_dir.resolve()
    settings.frontend_dist_dir = settings.frontend_dist_dir.resolve()
    settings.profiles_path = settings.profiles_path.resolve()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    settings.jobs_db_path.parent.mkdir(parents=True, exist_ok=True)
    return settings

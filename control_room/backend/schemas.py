from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from .docs import DocInfo
from .jobs import JobAction, JobRecord
from .profiles import ProfilesConfig


class APIError(BaseModel):
    code: str
    message: str
    hint: Optional[str] = None


class APIEnvelope(BaseModel):
    api_version: str = "v1"
    data: Any = None
    error: Optional[APIError] = None


class ProfilesPayload(BaseModel):
    profiles: ProfilesConfig


class DocsPayload(BaseModel):
    docs: List[DocInfo]


class DocDetailPayload(BaseModel):
    doc: DocInfo


class FilesPayload(BaseModel):
    files: List[Dict[str, Any]]


class SuggestedPayload(BaseModel):
    rules: List[Dict[str, Any]]
    etag: Optional[str]


class PreviewPayload(BaseModel):
    preview: Dict[str, Any]


class JobPayload(BaseModel):
    job: JobRecord


class JobsPayload(BaseModel):
    jobs: List[JobRecord]


class CommandPreviewPayload(BaseModel):
    action: JobAction
    argv: List[str]
    cwd: Optional[Path]
    doc_id: Optional[str]
    profile_id: Optional[str]
    artifacts: List[str]


class LogPayload(BaseModel):
    job_id: int
    log: str


class CancelPayload(BaseModel):
    canceled: bool


class HealthPayload(BaseModel):
    data_pipeline_root: str
    data_pipeline_root_exists: bool
    data_pipeline_root_hint: Optional[str] = None
    ts_repo_root: str
    ts_repo_root_exists: bool
    ts_repo_root_hint: Optional[str] = None
    run_bat_path: str
    run_bat_exists: bool
    run_bat_hint: Optional[str] = None
    ts_venv_dir: Optional[str] = None
    ts_venv_exists: bool
    ts_venv_hint: Optional[str] = None
    logs_dir: str
    logs_dir_exists: bool
    logs_dir_hint: Optional[str] = None
    jobs_db_path: str
    jobs_db_exists: bool
    jobs_db_hint: Optional[str] = None
    queued_jobs: int
    running_jobs: int
    succeeded_jobs: int
    failed_jobs: int
    canceled_jobs: int
    ws_enabled: bool
    api_key_enabled: bool
    git_sha: str


class StorageDirPayload(BaseModel):
    label: str
    path: str
    exists: bool
    size_bytes: int
    items: int
    oldest: Optional[str] = None
    newest: Optional[str] = None


class HeavyDocPayload(BaseModel):
    doc_id: str
    size_bytes: int
    location: str


class StoragePayload(BaseModel):
    root: str
    directories: List[StorageDirPayload]
    heavy_docs: List[HeavyDocPayload]
    orphans: Dict[str, List[str]]

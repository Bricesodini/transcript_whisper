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

    class Config:
        json_encoders = {Path: lambda v: str(v) if v else None}


class LogPayload(BaseModel):
    job_id: int
    log: str


class CancelPayload(BaseModel):
    canceled: bool

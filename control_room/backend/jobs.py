from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from .errors import FailureReason


class JobAction(str, Enum):
    ASR_BATCH = "asr_batch"
    LEXICON_BATCH = "lexicon_batch"
    LEXICON_SCAN = "lexicon_scan"
    LEXICON_APPLY = "lexicon_apply"
    RAG_BATCH = "rag_batch"
    RAG_EXPORT = "rag_export"
    RAG_DOCTOR = "rag_doctor"
    RAG_QUERY = "rag_query"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAIL = "fail"
    CANCELED = "canceled"


class JobBase(BaseModel):
    action: JobAction
    argv: List[str] = Field(..., description="Command executed for the job.")
    cwd: Optional[Path] = None
    doc_id: Optional[str] = None
    profile_id: Optional[str] = None
    write_lock: bool = False
    artifacts: List[str] = Field(default_factory=list)


class JobCreate(JobBase):
    job_version: int = 1


class JobRecord(JobBase):
    id: int
    status: JobStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    log_path: Path
    exit_code: Optional[int] = None
    job_version: int = 1
    failure_type: FailureReason = FailureReason.NONE
    failure_hint: Optional[str] = None

    class Config:
        json_encoders = {
            Path: lambda v: str(v),
            datetime: lambda v: v.isoformat(),
        }


def serialize_command(cmd: List[str]) -> str:
    return json.dumps(cmd, ensure_ascii=False)


def deserialize_command(raw: str) -> List[str]:
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(part) for part in data]
    except json.JSONDecodeError:
        pass
    return []


def serialize_artifacts(artifacts: List[str]) -> str:
    return json.dumps(artifacts, ensure_ascii=False)


def deserialize_artifacts(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(item) for item in data]
    except json.JSONDecodeError:
        pass
    return []

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel

from .errors import FailureReason
from .jobs import JobAction, JobRecord, JobStatus
from .resolver import DocPaths, list_doc_paths
from .runner import JobManager
from .settings import Settings


class StampInfo(BaseModel):
    doc: Optional[str] = None
    source_file: Optional[str] = None
    source_sha256: Optional[str] = None
    rules_count: Optional[int] = None
    updated_at_utc: Optional[str] = None


class DocState(str, Enum):
    missing = "MISSING"
    asr_ready = "ASR_READY"
    lexicon_suggested = "LEXICON_SUGGESTED"
    lexicon_validated = "LEXICON_VALIDATED"
    rag_ready = "RAG_READY"
    rag_failed = "RAG_FAILED"


class JobSummary(BaseModel):
    id: int
    action: JobAction
    status: JobStatus
    failure_type: FailureReason
    failure_hint: Optional[str]
    created_at: datetime
    ended_at: Optional[datetime]


class DocInfo(BaseModel):
    name: str
    work_dir: Optional[Path]
    transcript_dir: Optional[Path]
    suggested_path: Optional[Path]
    validated_path: Optional[Path]
    has_suggested: bool
    has_validated: bool
    has_stamp: bool
    suggested_count: int
    stamp: Optional[StampInfo]
    doc_state: DocState
    rag_versions: List[str]
    last_rag_version: Optional[str]
    rag_ready: bool
    last_job: Optional[JobSummary]
    last_rag_job: Optional[JobSummary]
    allowed_actions: List["AllowedAction"]
    locked: bool
    locked_by_job_id: Optional[int]
    locked_action: Optional[JobAction]
    suggested_etag: Optional[str]


def scan_documents(settings: Settings, jobs: JobManager) -> List[DocInfo]:
    return [build_doc_info(settings, jobs, doc_paths) for doc_paths in list_doc_paths(settings)]


def build_doc_info(settings: Settings, jobs: JobManager, doc_paths: DocPaths) -> DocInfo:
    suggested = doc_paths.suggested_glossary
    validated = doc_paths.validated_glossary
    stamp_path = doc_paths.stamp_path
    has_suggested = bool(suggested and suggested.exists())
    has_validated = bool(validated and validated.exists())
    stamp = read_stamp(stamp_path) if stamp_path and stamp_path.exists() else None
    suggested_count = count_rules(suggested) if has_suggested else 0
    rag_doc_root = settings.rag_output_dir / f"RAG-{doc_paths.doc_id}"
    rag_versions = (
        sorted(
            [child.name for child in rag_doc_root.iterdir() if child.is_dir()],
            reverse=True,
        )
        if rag_doc_root.exists()
        else []
    )
    rag_ready = bool(rag_versions)
    last_version = rag_versions[0] if rag_versions else None
    last_job = jobs.last_job_for_doc(doc_paths.doc_id)
    last_rag_job = jobs.last_job_for_doc(
        doc_paths.doc_id,
        [JobAction.RAG_BATCH, JobAction.RAG_EXPORT, JobAction.RAG_DOCTOR],
    )
    lock_state = jobs.get_doc_lock_state(doc_paths.doc_id)
    doc_state = determine_doc_state(
        DocStateInputs(
            has_work=bool(doc_paths.work_dir and doc_paths.work_dir.exists()),
            has_transcript=bool(
                doc_paths.transcript_dir and doc_paths.transcript_dir.exists()
            ),
            has_suggested=has_suggested,
            has_validated=has_validated,
            rag_ready=rag_ready,
            rag_failed=bool(
                last_rag_job
                and last_rag_job.status == JobStatus.FAIL
                and last_rag_job.failure_type != FailureReason.NONE
            ),
        )
    )
    allowed_actions = compute_allowed_actions(doc_state, lock_state.locked)
    etag = compute_etag(suggested) if has_suggested else None
    return DocInfo(
        name=doc_paths.doc_id,
        work_dir=doc_paths.work_dir,
        transcript_dir=doc_paths.transcript_dir,
        suggested_path=suggested,
        validated_path=validated,
        has_suggested=has_suggested,
        has_validated=has_validated,
        has_stamp=stamp is not None,
        suggested_count=suggested_count,
        stamp=stamp,
        doc_state=doc_state,
        rag_versions=rag_versions,
        last_rag_version=last_version,
        rag_ready=rag_ready,
        last_job=_job_summary(last_job) if last_job else None,
        last_rag_job=_job_summary(last_rag_job) if last_rag_job else None,
        allowed_actions=allowed_actions,
        locked=lock_state.locked,
        locked_by_job_id=lock_state.job_id,
        locked_action=lock_state.action,
        suggested_etag=etag,
    )


@dataclass
class DocStateInputs:
    has_work: bool
    has_transcript: bool
    has_suggested: bool
    has_validated: bool
    rag_ready: bool
    rag_failed: bool


def determine_doc_state(inputs: DocStateInputs) -> DocState:
    has_sources = inputs.has_work and inputs.has_transcript
    if not has_sources:
        return DocState.missing
    if inputs.rag_ready:
        return DocState.rag_ready
    if inputs.rag_failed:
        return DocState.rag_failed
    if inputs.has_validated:
        return DocState.lexicon_validated
    if inputs.has_suggested:
        return DocState.lexicon_suggested
    return DocState.asr_ready


class AllowedAction(str, Enum):
    LEXICON_SCAN = "lexicon_scan"
    LEXICON_APPLY = "lexicon_apply"
    RAG_EXPORT = "rag_export"
    RAG_DOCTOR = "rag_doctor"
    RAG_QUERY = "rag_query"


ALLOWED_ACTIONS_BY_STATE: Dict[DocState, List[AllowedAction]] = {
    DocState.missing: [],
    DocState.asr_ready: [AllowedAction.LEXICON_SCAN],
    DocState.lexicon_suggested: [
        AllowedAction.LEXICON_SCAN,
        AllowedAction.LEXICON_APPLY,
    ],
    DocState.lexicon_validated: [
        AllowedAction.LEXICON_SCAN,
        AllowedAction.LEXICON_APPLY,
        AllowedAction.RAG_EXPORT,
        AllowedAction.RAG_DOCTOR,
        AllowedAction.RAG_QUERY,
    ],
    DocState.rag_ready: [
        AllowedAction.LEXICON_SCAN,
        AllowedAction.LEXICON_APPLY,
        AllowedAction.RAG_EXPORT,
        AllowedAction.RAG_DOCTOR,
        AllowedAction.RAG_QUERY,
    ],
    DocState.rag_failed: [
        AllowedAction.LEXICON_SCAN,
        AllowedAction.LEXICON_APPLY,
        AllowedAction.RAG_EXPORT,
        AllowedAction.RAG_DOCTOR,
        AllowedAction.RAG_QUERY,
    ],
}


def compute_allowed_actions(doc_state: DocState, locked: bool) -> List[AllowedAction]:
    if locked:
        return []
    return list(ALLOWED_ACTIONS_BY_STATE.get(doc_state, []))


class DocLockInfo(BaseModel):
    locked: bool
    job_id: Optional[int]
    action: Optional[JobAction]


def build_lock_info(jobs: JobManager, doc_id: str) -> DocLockInfo:
    state = jobs.get_doc_lock_state(doc_id)
    return DocLockInfo(locked=state.locked, job_id=state.job_id, action=state.action)


def _job_summary(job: Optional[JobRecord]) -> Optional[JobSummary]:
    if not job:
        return None
    return JobSummary(
        id=job.id,
        action=job.action,
        status=job.status,
        failure_type=job.failure_type,
        failure_hint=job.failure_hint,
        created_at=job.created_at,
        ended_at=job.ended_at,
    )


def read_stamp(path: Path) -> Optional[StampInfo]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return StampInfo(**data)
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return None


def count_rules(path: Optional[Path]) -> int:
    if not path or not path.exists():
        return 0
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return 0
    if isinstance(content, dict):
        rules = content.get("rules", [])
        if isinstance(rules, list):
            return len(rules)
    return 0


def load_rules(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return []
    rules = content.get("rules") if isinstance(content, dict) else None
    if not isinstance(rules, list):
        return []
    cleaned = []
    for rule in rules:
        if isinstance(rule, dict):
            cleaned.append(rule)
    return cleaned


def save_validated_glossary(
    path: Path,
    doc_id: str,
    rules: List[Dict],
    *,
    expected_etag: Optional[str],
    current_etag: Optional[str],
) -> None:
    if expected_etag and expected_etag != (current_etag or ""):
        raise GlossaryValidationError(
            "Le fichier suggested a changé. Rafraîchir avant de sauvegarder."
        )
    cleaned_rules = _normalize_rules(rules)
    payload = {
        "version": 1,
        "doc_id": doc_id,
        "rules": cleaned_rules,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    if len(serialized.encode("utf-8")) > 200_000:
        raise GlossaryValidationError("Fichier de glossaire trop volumineux.")
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
    if path.exists():
        backup = path.with_name(
            f"{path.name}.bak.{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(path, backup)
    os.replace(tmp_path, path)


def list_key_files(doc_paths: DocPaths) -> List[Dict[str, object]]:
    files: List[Tuple[str, Optional[Path]]] = [
        ("05_polished.json", doc_paths.work_dir / "05_polished.json" if doc_paths.work_dir else None),
        ("04_cleaned.json", doc_paths.work_dir / "04_cleaned.json" if doc_paths.work_dir else None),
        ("02_merged_raw.json", doc_paths.work_dir / "02_merged_raw.json" if doc_paths.work_dir else None),
        ("rag.glossary.suggested.yaml", doc_paths.suggested_glossary),
        ("rag.glossary.yaml", doc_paths.validated_glossary),
        (".lexicon_ok.json", doc_paths.stamp_path),
    ]
    result: List[Dict[str, object]] = []
    for name, path in files:
        if path and path.exists():
            stat = path.stat()
            result.append(
                {
                    "name": name,
                    "path": str(path),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
    return result


def load_preview_text(doc_paths: DocPaths, limit: int = 1200) -> str:
    transcript_dir = doc_paths.transcript_dir
    if transcript_dir:
        candidates = list(transcript_dir.glob("*.clean.txt"))
        if not candidates:
            candidates = list(transcript_dir.glob("*.txt"))
        if candidates:
            text = candidates[0].read_text(encoding="utf-8", errors="replace")
            return text[:limit]
    if doc_paths.work_dir:
        chunks = doc_paths.work_dir / "chunks.jsonl"
        if chunks.exists():
            with chunks.open("r", encoding="utf-8") as handle:
                for line in handle:
                    snippet = line.strip()
                    if snippet:
                        return snippet[:limit]
    return ""


class GlossaryValidationError(ValueError):
    pass


def _normalize_rules(rules: List[Dict]) -> List[Dict]:
    if len(rules) > 500:
        raise GlossaryValidationError("Trop de règles (max 500).")
    dedup: Dict[Tuple[str, str], Dict] = {}
    for idx, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise GlossaryValidationError(f"Règle invalide (index {idx}).")
        pattern = rule.get("pattern")
        replacement = rule.get("replacement", "")
        if not isinstance(pattern, str) or not pattern.strip():
            raise GlossaryValidationError(f"Pattern manquant (index {idx}).")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise GlossaryValidationError(f"Regex invalide ({pattern}): {exc}") from exc
        key = (pattern, str(replacement))
        dedup[key] = {
            "pattern": pattern,
            "replacement": str(replacement),
            "confidence": rule.get("confidence"),
            "evidence": rule.get("evidence", []),
        }
    sorted_keys = sorted(dedup.keys(), key=lambda item: (item[0], item[1]))
    return [dedup[key] for key in sorted_keys]


def compute_etag(path: Optional[Path]) -> Optional[str]:
    if not path or not path.exists():
        return None
    return sha256(path.read_bytes()).hexdigest()

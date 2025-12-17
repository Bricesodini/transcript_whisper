from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from fastapi import WebSocket

from .errors import FailureReason, classify_failure_from_log
from .exceptions import DocBusyError
from .jobs import (
    JobAction,
    JobCreate,
    JobRecord,
    JobStatus,
    deserialize_artifacts,
    deserialize_command,
    serialize_artifacts,
    serialize_command,
)
from .resolver import DOC_LOCK_GLOBAL
from .settings import Settings

JOB_SCHEMA_VERSION = 1


@dataclass
class LockState:
    locked: bool
    job_id: Optional[int]
    action: Optional[JobAction]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStore:
    """SQLite wrapper managing job metadata."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    cmd TEXT NOT NULL,
                    cwd TEXT,
                    doc TEXT,
                    log_path TEXT,
                    exit_code INTEGER,
                    job_version INTEGER DEFAULT 1,
                    failure_reason TEXT,
                    failure_hint TEXT,
                    write_lock INTEGER DEFAULT 0,
                    profile_id TEXT,
                    artifacts TEXT,
                    duration_ms INTEGER
                )
                """
            )
            conn.commit()
            self._ensure_columns(conn)
            self._ensure_schema_version(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        info = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}  # type: ignore[index]
        alters = {
            "profile_id": "ALTER TABLE jobs ADD COLUMN profile_id TEXT",
            "artifacts": "ALTER TABLE jobs ADD COLUMN artifacts TEXT",
            "duration_ms": "ALTER TABLE jobs ADD COLUMN duration_ms INTEGER",
        }
        for column, ddl in alters.items():
            if column not in info:
                conn.execute(ddl)
        conn.commit()

    def _ensure_schema_version(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS job_schema_version (
                version INTEGER NOT NULL
            )
            """
        )
        row = conn.execute("SELECT version FROM job_schema_version LIMIT 1").fetchone()
        if not row:
            conn.execute("INSERT INTO job_schema_version (version) VALUES (?)", (JOB_SCHEMA_VERSION,))
            conn.commit()
            return
        current = int(row["version"])
        if current == JOB_SCHEMA_VERSION:
            return
        if current > JOB_SCHEMA_VERSION:
            raise RuntimeError("jobs.db schema version is newer than supported.")
        self._apply_migrations(conn, current)

    def _apply_migrations(self, conn: sqlite3.Connection, current_version: int) -> None:
        version = current_version
        if version < 1:
            version = 1
        conn.execute("UPDATE job_schema_version SET version = ?", (version,))
        conn.commit()

    def insert_job(self, payload: JobCreate, log_path: Path) -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO jobs(
                    action, status, created_at, cmd, cwd, doc, log_path,
                    job_version, write_lock, profile_id, artifacts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.action.value,
                    JobStatus.QUEUED.value,
                    _utcnow().isoformat(),
                    serialize_command(payload.argv),
                    str(payload.cwd) if payload.cwd else None,
                    payload.doc_id,
                    str(log_path),
                    payload.job_version,
                    1 if payload.write_lock else 0,
                    payload.profile_id,
                    serialize_artifacts(payload.artifacts),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)

    def update_status(
        self,
        job_id: int,
        *,
        status: JobStatus,
        started_at: Optional[datetime] = None,
        ended_at: Optional[datetime] = None,
        exit_code: Optional[int] = None,
        failure_reason: Optional[FailureReason] = None,
        failure_hint: Optional[str] = None,
    ) -> None:
        fields = ["status = ?"]
        values: List[object] = [status.value]
        if started_at is not None:
            fields.append("started_at = ?")
            values.append(started_at.isoformat())
        if ended_at is not None:
            fields.append("ended_at = ?")
            values.append(ended_at.isoformat())
        if exit_code is not None:
            fields.append("exit_code = ?")
            values.append(exit_code)
        if failure_reason is not None:
            fields.append("failure_reason = ?")
            values.append(failure_reason.value)
        if failure_hint is not None:
            fields.append("failure_hint = ?")
            values.append(failure_hint)
        values.append(job_id)
        with self._get_conn() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()

    def update_duration(self, job_id: int, duration_ms: Optional[int]) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET duration_ms = ? WHERE id = ?", (duration_ms, job_id)
            )
            conn.commit()

    def get_job(self, job_id: int) -> Optional[JobRecord]:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = cur.fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def list_jobs(self, limit: int = 100) -> List[JobRecord]:
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (int(limit),)
            )
            rows = cur.fetchall()
        return [self._row_to_record(row) for row in rows]

    def last_job_for_doc(
        self, doc: str, actions: Optional[List[JobAction]] = None
    ) -> Optional[JobRecord]:
        query = "SELECT * FROM jobs WHERE doc = ?"
        params: List[object] = [doc]
        if actions:
            placeholders = ", ".join("?" for _ in actions)
            query += f" AND action IN ({placeholders})"
            params.extend(a.value for a in actions)
        query += " ORDER BY id DESC LIMIT 1"
        with self._get_conn() as conn:
            cur = conn.execute(query, params)
            row = cur.fetchone()
        if not row:
            return None
        return self._row_to_record(row)

    def _row_to_record(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=int(row["id"]),
            action=JobAction(row["action"]),
            status=JobStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"])
            if row["started_at"]
            else None,
            ended_at=datetime.fromisoformat(row["ended_at"])
            if row["ended_at"]
            else None,
            argv=deserialize_command(row["cmd"]),
            cwd=Path(row["cwd"]) if row["cwd"] else None,
            doc_id=row["doc"],
            log_path=Path(row["log_path"]) if row["log_path"] else Path(),
            exit_code=row["exit_code"],
            job_version=row["job_version"] or 1,
            failure_type=FailureReason(row["failure_reason"] or FailureReason.NONE.value),
            failure_hint=row["failure_hint"],
            write_lock=bool(row["write_lock"]),
            profile_id=row["profile_id"],
            artifacts=deserialize_artifacts(row["artifacts"]),
            duration_ms=row["duration_ms"],
        )


class WebSocketHub:
    """Broadcast log lines to websocket subscribers per job."""

    def __init__(self) -> None:
        self._subscribers: Dict[int, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def register(self, job_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers[job_id].add(websocket)

    async def unregister(self, job_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            if job_id in self._subscribers:
                self._subscribers[job_id].discard(websocket)
                if not self._subscribers[job_id]:
                    del self._subscribers[job_id]

    async def broadcast(self, job_id: int, message: str) -> None:
        async with self._lock:
            subscribers = list(self._subscribers.get(job_id, set()))
        for websocket in subscribers:
            try:
                await websocket.send_text(message)
            except Exception:
                await self.unregister(job_id, websocket)


class JobManager:
    """Create, run and introspect jobs."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = JobStore(settings.jobs_db_path)
        self.logs_dir = settings.logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.ws_hub = WebSocketHub()
        self._tasks: Dict[int, asyncio.Task] = {}
        self._processes: Dict[int, asyncio.subprocess.Process] = {}
        self._canceled: Set[int] = set()
        self._doc_locks: Dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._doc_lock_owner: Dict[str, int] = {}
        self._doc_lock_action: Dict[str, JobAction] = {}
        self._semaphore = asyncio.Semaphore(settings.max_workers)

    def create_job(self, payload: JobCreate) -> JobRecord:
        if payload.doc_id and not payload.write_lock:
            locked, job_id = self.is_doc_locked(payload.doc_id)
            if locked:
                raise DocBusyError(payload.doc_id, job_id)
        log_path = self.logs_dir / f"job_{int(_utcnow().timestamp() * 1000)}.log"
        job_id = self.store.insert_job(payload, log_path)
        job = self.store.get_job(job_id)
        if job is None:
            raise RuntimeError("Failed to fetch job after insertion")
        return job

    def list_jobs(self, limit: int = 100) -> List[JobRecord]:
        return self.store.list_jobs(limit=limit)

    def get_job(self, job_id: int) -> Optional[JobRecord]:
        return self.store.get_job(job_id)

    def last_job_for_doc(
        self, doc: str, actions: Optional[List[JobAction]] = None
    ) -> Optional[JobRecord]:
        return self.store.last_job_for_doc(doc, actions)

    def read_log(self, job_id: int, max_bytes: int = 200_000) -> str:
        job = self.get_job(job_id)
        if not job or not job.log_path.exists():
            return ""
        data = job.log_path.read_text(encoding="utf-8", errors="replace")
        if len(data) > max_bytes:
            return data[-max_bytes:]
        return data

    def log_file_path(self, job_id: int) -> Optional[Path]:
        job = self.get_job(job_id)
        if not job:
            return None
        return job.log_path if job.log_path.exists() else None

    def schedule(self, job_id: int) -> None:
        loop = asyncio.get_event_loop()
        task = loop.create_task(self._run_job(job_id))
        self._tasks[job_id] = task
        task.add_done_callback(lambda _, jid=job_id: self._tasks.pop(jid, None))

    async def cancel_job(self, job_id: int) -> bool:
        self._canceled.add(job_id)
        process = self._processes.get(job_id)
        if process:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            return True
        job = self.get_job(job_id)
        if job and job.status == JobStatus.QUEUED:
            self.store.update_status(
                job_id,
                status=JobStatus.CANCELED,
                ended_at=_utcnow(),
                failure_reason=FailureReason.CANCELED,
                failure_hint="Job annulé avant démarrage.",
            )
            return True
        return False

    def is_doc_locked(self, doc_id: str) -> tuple[bool, Optional[int]]:
        state = self.get_doc_lock_state(doc_id)
        return state.locked, state.job_id

    def get_doc_lock_state(self, doc_id: str) -> LockState:
        job_id = self._doc_lock_owner.get(doc_id)
        action = self._doc_lock_action.get(doc_id)
        return LockState(
            locked=job_id is not None,
            job_id=job_id,
            action=action,
        )

    async def _run_job(self, job_id: int) -> None:
        job = self.store.get_job(job_id)
        if not job:
            return
        doc_key = job.doc_id or DOC_LOCK_GLOBAL
        doc_lock = self._doc_locks[doc_key] if job.write_lock else None
        await self._semaphore.acquire()
        if doc_lock:
            await doc_lock.acquire()
            if job.doc_id:
                self._doc_lock_owner[job.doc_id] = job_id
                self._doc_lock_action[job.doc_id] = job.action
        start_time: Optional[datetime] = None
        try:
            if job_id in self._canceled:
                self.store.update_status(
                    job_id,
                    status=JobStatus.CANCELED,
                    ended_at=_utcnow(),
                    failure_reason=FailureReason.CANCELED,
                    failure_hint="Job annulé avant démarrage.",
                )
                return
            start_time = _utcnow()
            self.store.update_status(
                job_id, status=JobStatus.RUNNING, started_at=start_time
            )
            env = dict(os.environ)
            env.setdefault("PYTHONIOENCODING", "utf-8")
            env.setdefault("DATA_PIPELINE_ROOT", str(self.settings.data_pipeline_root))
            if self.settings.ts_venv_dir:
                env["TS_VENV_DIR"] = str(self.settings.ts_venv_dir)
            cwd = str(job.cwd) if job.cwd else str(self.settings.ts_repo_root)
            log_path = job.log_path
            log_path.parent.mkdir(parents=True, exist_ok=True)
            process = await asyncio.create_subprocess_exec(
                *job.argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
            self._processes[job_id] = process
            log_lock = asyncio.Lock()
            tasks: List[asyncio.Task] = []
            if process.stdout:
                tasks.append(
                    asyncio.create_task(
                        self._pipe_stream(process.stdout, job_id, log_path, log_lock)
                    )
                )
            if process.stderr:
                tasks.append(
                    asyncio.create_task(
                        self._pipe_stream(
                            process.stderr,
                            job_id,
                            log_path,
                            log_lock,
                            prefix="[stderr] ",
                        )
                    )
                )
            return_code = await process.wait()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            canceled = job_id in self._canceled and return_code != 0
            failure_reason, failure_hint = (FailureReason.NONE, None)
            status = JobStatus.SUCCESS
            if return_code != 0 or canceled:
                status = JobStatus.CANCELED if canceled else JobStatus.FAIL
                log_tail = self.read_log(job_id, 4000)
                failure_reason, failure_hint = classify_failure_from_log(
                    log_tail, return_code, canceled
                )
            ended_at = _utcnow()
            self.store.update_status(
                job_id,
                status=status,
                ended_at=ended_at,
                exit_code=return_code,
                failure_reason=failure_reason,
                failure_hint=failure_hint,
            )
            if start_time:
                duration_ms = int((ended_at - start_time).total_seconds() * 1000)
                self.store.update_duration(job_id, duration_ms)
            await self.ws_hub.broadcast(
                job_id, f"[control] Job terminé (code {return_code})"
            )
        finally:
            if doc_lock:
                doc_lock.release()
                if job.doc_id:
                    self._doc_lock_owner.pop(job.doc_id, None)
                    self._doc_lock_action.pop(job.doc_id, None)
            self._semaphore.release()
            self._processes.pop(job_id, None)
            self._canceled.discard(job_id)

    async def _pipe_stream(
        self,
        stream: asyncio.StreamReader,
        job_id: int,
        log_path: Path,
        log_lock: asyncio.Lock,
        *,
        prefix: str = "",
    ) -> None:
        while not stream.at_eof():
            chunk = await stream.readline()
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace").rstrip("\r\n")
            if prefix:
                text = f"{prefix}{text}"
            async with log_lock:
                with log_path.open("a", encoding="utf-8") as handle:
                    handle.write(text + "\n")
            await self.ws_hub.broadcast(job_id, text)

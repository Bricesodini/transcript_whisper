from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from functools import lru_cache
from pydantic import BaseModel, Field

from .commands import (
    CommandError,
    build_asr_batch_command,
    build_lexicon_apply_command,
    build_lexicon_batch_command,
    build_lexicon_scan_command,
    build_rag_batch_command,
    build_rag_doctor_command,
    build_rag_export_command,
    build_rag_query_command,
)
from .docs import (
    DocInfo,
    GlossaryValidationError,
    build_doc_info,
    compute_etag,
    list_key_files,
    load_preview_text,
    load_rules,
    save_validated_glossary,
    scan_documents,
)
from .exceptions import DocBusyError
from .jobs import JobAction, JobCreate, JobRecord
from .preview import preview_with_timeout
from .profiles import ProfilesConfig, load_profiles
from .resolver import DocPaths, ResolverError, resolve_doc
from .runner import JobManager
from .storage import collect_storage_snapshot
from .schemas import (
    APIEnvelope,
    APIError,
    CancelPayload,
    CommandPreviewPayload,
    DocDetailPayload,
    DocsPayload,
    FilesPayload,
    HealthPayload,
    JobPayload,
    JobsPayload,
    LogPayload,
    PreviewPayload,
    ProfilesPayload,
    SuggestedPayload,
    StoragePayload,
)
from .settings import get_settings

API_VERSION = "v1"

settings = get_settings()
profiles_config = load_profiles(settings)
job_manager = JobManager(settings)

api_key_header = APIKeyHeader(name="X-API-KEY", auto_error=False)


async def verify_api_key(api_key: Optional[str] = Depends(api_key_header)) -> None:
    if settings.api_key and api_key != settings.api_key:
        raise_api_error("unauthorized", "Invalid API key", status_code=401)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Transcribe Control Room",
        version="0.3.0",
        description="Web control plane for ASR / Lexicon / RAG batch pipelines.",
        dependencies=[Depends(verify_api_key)],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if settings.frontend_dist_dir.exists():
        app.mount(
            "/static",
            StaticFiles(directory=settings.frontend_dist_dir),
            name="static",
    )

    register_routes(app)
    register_exception_handlers(app)
    return app


def success(data: Any = None) -> APIEnvelope:
    return APIEnvelope(data=data)


def raise_api_error(code: str, message: str, *, hint: Optional[str] = None, status_code: int = 400) -> None:
    raise HTTPException(status_code=status_code, detail={"code": code, "message": message, "hint": hint})


class GlossaryPayload(BaseModel):
    doc_id: Optional[str] = None
    rules: List[Dict[str, Any]] = Field(default_factory=list)
    etag: Optional[str] = None


class AsrBatchRequest(BaseModel):
    profile: Optional[str] = None


class LexiconBatchRequest(BaseModel):
    force: bool = False
    scan_only: bool = True
    apply: bool = False
    doc: Optional[str] = None
    max_docs: Optional[int] = None
    profile: Optional[str] = None


class RagBatchRequest(BaseModel):
    doc: Optional[str] = None
    version_tag: Optional[str] = None
    query: Optional[str] = None
    force: bool = False
    profile: Optional[str] = None


class LexiconDocRequest(BaseModel):
    doc: str
    profile: Optional[str] = None


class RagExportRequest(BaseModel):
    doc: str
    force: bool = False
    version_tag: Optional[str] = None
    profile: Optional[str] = None


class RagDoctorRequest(BaseModel):
    doc: str
    version_tag: Optional[str] = None
    profile: Optional[str] = None


class RagQueryRequest(BaseModel):
    doc: str
    query: str
    top_k: Optional[int] = None
    version_tag: Optional[str] = None
    profile: Optional[str] = None


def register_routes(app: FastAPI) -> None:
    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/health", response_model=APIEnvelope)
    async def api_health() -> APIEnvelope:
        return success(_build_health_payload())

    @app.get("/api/v1/storage", response_model=APIEnvelope)
    async def storage_snapshot() -> APIEnvelope:
        snapshot = collect_storage_snapshot(settings)
        return success(StoragePayload(**snapshot))

    @app.get("/api/v1/profiles", response_model=APIEnvelope)
    async def get_profiles() -> APIEnvelope:
        return success(ProfilesPayload(profiles=profiles_config))

    @app.get("/api/v1/docs", response_model=APIEnvelope)
    async def list_docs_route() -> APIEnvelope:
        docs = scan_documents(settings, job_manager)
        return success(DocsPayload(docs=docs))

    @app.get("/api/v1/docs/{doc_name}", response_model=APIEnvelope)
    async def get_doc(doc_name: str) -> APIEnvelope:
        doc_paths = _get_doc_paths(doc_name)
        doc_info = build_doc_info(settings, job_manager, doc_paths)
        return success(DocDetailPayload(doc=doc_info))

    @app.get("/api/v1/docs/{doc_name}/files", response_model=APIEnvelope)
    async def doc_files(doc_name: str) -> APIEnvelope:
        doc_paths = _get_doc_paths(doc_name)
        return success(FilesPayload(files=list_key_files(doc_paths)))

    @app.get("/api/v1/docs/{doc_name}/suggested", response_model=APIEnvelope)
    async def doc_suggested(doc_name: str) -> APIEnvelope:
        doc_paths = _get_doc_paths(doc_name)
        suggested = doc_paths.suggested_glossary
        rules = load_rules(suggested) if suggested and suggested.exists() else []
        etag = compute_etag(suggested) if suggested else None
        return success(SuggestedPayload(rules=rules, etag=etag))

    @app.put("/api/v1/docs/{doc_name}/validated", response_model=APIEnvelope)
    async def save_validated(doc_name: str, payload: GlossaryPayload) -> APIEnvelope:
        doc_paths = _get_doc_paths(doc_name)
        if not doc_paths.work_dir:
            raise_api_error("work_dir_missing", "work_dir introuvable")
        validated_path = doc_paths.work_dir / "rag.glossary.yaml"
        doc_id = payload.doc_id or doc_paths.doc_id
        current_etag = compute_etag(doc_paths.suggested_glossary)
        if payload.etag and payload.etag != (current_etag or ""):
            raise_api_error("suggested_changed", "Glossaire suggéré modifié. Rafraîchir.", status_code=409)
        try:
            save_validated_glossary(
                validated_path,
                doc_id,
                payload.rules,
                expected_etag=payload.etag,
                current_etag=current_etag,
            )
        except GlossaryValidationError as exc:
            raise_api_error("glossary_invalid", str(exc))
        return success({"doc": doc_id})

    @app.get("/api/v1/docs/{doc_name}/preview", response_model=APIEnvelope)
    async def doc_preview(
        doc_name: str, pattern: Optional[str] = None, replacement: Optional[str] = None
    ) -> APIEnvelope:
        doc_paths = _get_doc_paths(doc_name)
        source = load_preview_text(doc_paths)
        result = await preview_with_timeout(
            source,
            pattern,
            replacement,
            settings.preview_timeout_ms,
        )
        return success(PreviewPayload(preview=result))

    @app.post("/api/v1/run/asr-batch", response_model=APIEnvelope)
    async def run_asr_batch(payload: AsrBatchRequest) -> APIEnvelope:
        return _schedule(lambda: build_asr_batch_command(settings, profiles_config, profile=payload.profile))

    @app.post("/api/v1/run/asr-batch/dry-run", response_model=APIEnvelope)
    async def run_asr_batch_preview(payload: AsrBatchRequest) -> APIEnvelope:
        return _preview(lambda: build_asr_batch_command(settings, profiles_config, profile=payload.profile))

    @app.post("/api/v1/run/lexicon-batch", response_model=APIEnvelope)
    async def run_lexicon_batch(payload: LexiconBatchRequest) -> APIEnvelope:
        return _schedule(
            lambda: build_lexicon_batch_command(
                settings,
                profiles_config,
                force=payload.force,
                apply=payload.apply,
                scan_only=payload.scan_only,
                doc=payload.doc,
                max_docs=payload.max_docs,
                profile=payload.profile,
            )
        )

    @app.post("/api/v1/run/lexicon-batch/dry-run", response_model=APIEnvelope)
    async def run_lexicon_batch_preview(payload: LexiconBatchRequest) -> APIEnvelope:
        return _preview(
            lambda: build_lexicon_batch_command(
                settings,
                profiles_config,
                force=payload.force,
                apply=payload.apply,
                scan_only=payload.scan_only,
                doc=payload.doc,
                max_docs=payload.max_docs,
                profile=payload.profile,
            )
        )

    @app.post("/api/v1/run/rag-batch", response_model=APIEnvelope)
    async def run_rag_batch(payload: RagBatchRequest) -> APIEnvelope:
        return _schedule(
            lambda: build_rag_batch_command(
                settings,
                profiles_config,
                doc=payload.doc,
                version_tag=payload.version_tag,
                query=payload.query,
                force=payload.force,
                profile=payload.profile,
            )
        )

    @app.post("/api/v1/run/rag-batch/dry-run", response_model=APIEnvelope)
    async def run_rag_batch_preview(payload: RagBatchRequest) -> APIEnvelope:
        return _preview(
            lambda: build_rag_batch_command(
                settings,
                profiles_config,
                doc=payload.doc,
                version_tag=payload.version_tag,
                query=payload.query,
                force=payload.force,
                profile=payload.profile,
            )
        )

    @app.post("/api/v1/run/lexicon-scan", response_model=APIEnvelope)
    async def run_lexicon_scan(payload: LexiconDocRequest) -> APIEnvelope:
        return _schedule(
            lambda: build_lexicon_scan_command(
                settings, payload.doc, profile=payload.profile, profiles=profiles_config
            )
        )

    @app.post("/api/v1/run/lexicon-scan/dry-run", response_model=APIEnvelope)
    async def run_lexicon_scan_preview(payload: LexiconDocRequest) -> APIEnvelope:
        return _preview(
            lambda: build_lexicon_scan_command(
                settings, payload.doc, profile=payload.profile, profiles=profiles_config
            )
        )

    @app.post("/api/v1/run/lexicon-apply", response_model=APIEnvelope)
    async def run_lexicon_apply(payload: LexiconDocRequest) -> APIEnvelope:
        return _schedule(
            lambda: build_lexicon_apply_command(
                settings, payload.doc, profiles=profiles_config, profile=payload.profile
            )
        )

    @app.post("/api/v1/run/lexicon-apply/dry-run", response_model=APIEnvelope)
    async def run_lexicon_apply_preview(payload: LexiconDocRequest) -> APIEnvelope:
        return _preview(
            lambda: build_lexicon_apply_command(
                settings, payload.doc, profiles=profiles_config, profile=payload.profile
            )
        )

    @app.post("/api/v1/run/rag-export", response_model=APIEnvelope)
    async def run_rag_export(payload: RagExportRequest) -> APIEnvelope:
        return _schedule(
            lambda: build_rag_export_command(
                settings,
                profiles_config,
                payload.doc,
                version_tag=payload.version_tag,
                force=payload.force,
                profile=payload.profile,
            )
        )

    @app.post("/api/v1/run/rag-export/dry-run", response_model=APIEnvelope)
    async def run_rag_export_preview(payload: RagExportRequest) -> APIEnvelope:
        return _preview(
            lambda: build_rag_export_command(
                settings,
                profiles_config,
                payload.doc,
                version_tag=payload.version_tag,
                force=payload.force,
                profile=payload.profile,
            )
        )

    @app.post("/api/v1/run/rag-doctor", response_model=APIEnvelope)
    async def run_rag_doctor(payload: RagDoctorRequest) -> APIEnvelope:
        return _schedule(
            lambda: build_rag_doctor_command(
                settings,
                profiles_config,
                payload.doc,
                version_tag=payload.version_tag,
                profile=payload.profile,
            )
        )

    @app.post("/api/v1/run/rag-doctor/dry-run", response_model=APIEnvelope)
    async def run_rag_doctor_preview(payload: RagDoctorRequest) -> APIEnvelope:
        return _preview(
            lambda: build_rag_doctor_command(
                settings,
                profiles_config,
                payload.doc,
                version_tag=payload.version_tag,
                profile=payload.profile,
            )
        )

    @app.post("/api/v1/run/rag-query", response_model=APIEnvelope)
    async def run_rag_query(payload: RagQueryRequest) -> APIEnvelope:
        return _schedule(
            lambda: build_rag_query_command(
                settings,
                profiles_config,
                payload.doc,
                query=payload.query,
                version_tag=payload.version_tag,
                top_k=payload.top_k,
                profile=payload.profile,
            )
        )

    @app.post("/api/v1/run/rag-query/dry-run", response_model=APIEnvelope)
    async def run_rag_query_preview(payload: RagQueryRequest) -> APIEnvelope:
        return _preview(
            lambda: build_rag_query_command(
                settings,
                profiles_config,
                payload.doc,
                query=payload.query,
                version_tag=payload.version_tag,
                top_k=payload.top_k,
                profile=payload.profile,
            )
        )

    @app.get("/api/v1/jobs", response_model=APIEnvelope)
    async def list_jobs(limit: int = 100) -> APIEnvelope:
        jobs = job_manager.list_jobs(limit=limit)
        return success(JobsPayload(jobs=jobs))

    @app.get("/api/v1/jobs/{job_id}", response_model=APIEnvelope)
    async def job_detail(job_id: int) -> APIEnvelope:
        job = job_manager.get_job(job_id)
        if not job:
            raise_api_error("job_not_found", "Job introuvable", status_code=404)
        return success(JobPayload(job=job))

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=APIEnvelope)
    async def cancel_job(job_id: int) -> APIEnvelope:
        if not await job_manager.cancel_job(job_id):
            raise_api_error("job_not_cancelable", "Impossible d'annuler ce job", status_code=409)
        return success(CancelPayload(canceled=True))

    @app.get("/api/v1/jobs/{job_id}/log", response_model=APIEnvelope)
    async def job_log(job_id: int) -> APIEnvelope:
        log = job_manager.read_log(job_id)
        return success(LogPayload(job_id=job_id, log=log))

    @app.get("/api/v1/jobs/{job_id}/log/file")
    async def job_log_download(job_id: int):
        path = job_manager.log_file_path(job_id)
        if not path:
            raise_api_error("log_not_found", "Log introuvable", status_code=404)
        return FileResponse(path, filename=f"job_{job_id}.log")

    @app.websocket("/ws/jobs/{job_id}")
    async def job_log_stream(websocket: WebSocket, job_id: int):
        requested_protocol = websocket.headers.get("sec-websocket-protocol")
        if settings.api_key and requested_protocol != settings.api_key:
            await websocket.close(code=4401)
            return
        await websocket.accept(subprotocol=requested_protocol)
        job = job_manager.get_job(job_id)
        if job and job.log_path.exists():
            existing = job.log_path.read_text(encoding="utf-8", errors="replace")
            if existing:
                await websocket.send_text(existing)
        await job_manager.ws_hub.register(job_id, websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await job_manager.ws_hub.unregister(job_id, websocket)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        index_path = settings.frontend_dist_dir / "index.html"
        if index_path.exists():
            return FileResponse(index_path)
        raise HTTPException(status_code=404, detail="Not found")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail and "message" in detail:
            payload = APIEnvelope(
                data=None,
                error=APIError(
                    code=str(detail["code"]),
                    message=str(detail["message"]),
                    hint=detail.get("hint"),
                ),
            )
        else:
            payload = APIEnvelope(
                data=None,
                error=APIError(
                    code="http_error",
                    message=str(detail) if detail else "HTTP error",
                ),
            )
        return JSONResponse(status_code=exc.status_code, content=payload.dict())


def _build_job_payload(builder: Callable[[], JobCreate]) -> JobCreate:
    try:
        return builder()
    except CommandError as exc:
        raise_api_error("command_invalid", str(exc))
    except DocBusyError as exc:
        raise_api_error(
            "doc_busy",
            "Document verrouillÃ© par un autre job",
            hint=f"doc_id={exc.doc_id}, job_id={exc.job_id}",
            status_code=409,
        )


def _schedule(builder: Callable[[], JobCreate]) -> APIEnvelope:
    payload = _build_job_payload(builder)
    job = job_manager.create_job(payload)
    job_manager.schedule(job.id)
    return success(JobPayload(job=job))


def _preview(builder: Callable[[], JobCreate]) -> APIEnvelope:
    payload = _build_job_payload(builder)
    return success(
        CommandPreviewPayload(
            action=payload.action,
            argv=payload.argv,
            cwd=payload.cwd,
            doc_id=payload.doc_id,
            profile_id=payload.profile_id,
            artifacts=payload.artifacts,
        )
    )


def _get_doc_paths(doc_name: str) -> DocPaths:
    try:
        return resolve_doc(settings, doc_name)
    except ResolverError as exc:
        raise_api_error("doc_not_found", str(exc))


def _build_health_payload() -> HealthPayload:
    stats = job_manager.get_stats()
    (
        data_pipeline_root_path,
        data_root_exists,
        data_root_hint,
    ) = _path_state(settings.data_pipeline_root, "DATA_PIPELINE_ROOT")
    ts_repo_path, ts_repo_exists, ts_repo_hint = _path_state(settings.ts_repo_root, "TS_REPO_ROOT")
    run_bat_path, run_bat_exists, run_bat_hint = _path_state(settings.run_bat_path, "TS_RUN_BAT_PATH")
    jobs_db_path, jobs_db_exists, jobs_db_hint = _path_state(settings.jobs_db_path, "jobs.db")
    logs_dir_path, logs_dir_exists, logs_hint = _path_state(settings.logs_dir, "logs_dir")

    if settings.ts_venv_dir:
        ts_venv_path, ts_venv_exists, ts_venv_hint = _path_state(
            settings.ts_venv_dir, "TS_VENV_DIR"
        )
    else:
        ts_venv_path, ts_venv_exists, ts_venv_hint = (None, False, "TS_VENV_DIR non configurÃ©")

    return HealthPayload(
        data_pipeline_root=data_pipeline_root_path,
        data_pipeline_root_exists=data_root_exists,
        data_pipeline_root_hint=data_root_hint,
        ts_repo_root=ts_repo_path,
        ts_repo_root_exists=ts_repo_exists,
        ts_repo_root_hint=ts_repo_hint,
        run_bat_path=run_bat_path,
        run_bat_exists=run_bat_exists,
        run_bat_hint=run_bat_hint,
        ts_venv_dir=ts_venv_path,
        ts_venv_exists=ts_venv_exists,
        ts_venv_hint=ts_venv_hint,
        logs_dir=logs_dir_path,
        logs_dir_exists=logs_dir_exists,
        logs_dir_hint=logs_hint,
        jobs_db_path=jobs_db_path,
        jobs_db_exists=jobs_db_exists,
        jobs_db_hint=jobs_db_hint,
        queued_jobs=stats.queued,
        running_jobs=stats.running,
        succeeded_jobs=stats.succeeded,
        failed_jobs=stats.failed,
        canceled_jobs=stats.canceled,
        ws_enabled=True,
        api_key_enabled=bool(settings.api_key),
        git_sha=_git_sha(),
    )


def _path_state(path: Path, label: str) -> tuple[str, bool, Optional[str]]:
    resolved = str(path)
    exists = path.exists()
    hint = None if exists else f"{label} introuvable"
    return resolved, exists, hint


@lru_cache()
def _git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(settings.ts_repo_root),
            capture_output=True,
            text=True,
            check=True,
        )
        sha = result.stdout.strip()
        return sha or "unknown"
    except Exception:
        return "unknown"


app = create_app()

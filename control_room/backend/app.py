from __future__ import annotations

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
from .settings import get_settings

API_VERSION = "v1"

settings = get_settings()
profiles_config = load_profiles(settings)
job_manager = JobManager(settings)

api_key_header = APIKeyHeader(name="X-API-KEY", auto_error=False)


async def verify_api_key(api_key: Optional[str] = Depends(api_key_header)) -> None:
    if settings.api_key and api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


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
    return app


class APIEnvelope(BaseModel):
    api_version: str = API_VERSION
    ok: bool = True
    data: Any = None
    error: Optional[str] = None


class APIResponse(BaseModel):
    api_version: str = API_VERSION


class ProfilesResponse(APIResponse):
    profiles: ProfilesConfig


class DocsResponse(APIResponse):
    items: List[DocInfo]


class DocDetailResponse(APIResponse):
    doc: DocInfo


class FilesResponse(APIResponse):
    files: List[Dict[str, Any]]


class SuggestedResponse(APIResponse):
    rules: List[Dict[str, Any]]
    etag: Optional[str]


class PreviewResponse(APIResponse):
    preview: Dict[str, Any]


class JobResponse(APIResponse):
    job: JobRecord


class JobsResponse(APIResponse):
    jobs: List[JobRecord]


class CommandPreviewResponse(APIResponse):
    action: JobAction
    argv: List[str]
    cwd: Optional[Path]
    doc_id: Optional[str]
    profile_id: Optional[str]
    artifacts: List[str]

    class Config:
        json_encoders = {Path: lambda v: str(v) if v else None}


class LogResponse(APIResponse):
    job_id: int
    log: str


class CancelResponse(APIResponse):
    canceled: bool


class CommandPreviewData(BaseModel):
    action: JobAction
    argv: List[str]
    cwd: Optional[Path]
    doc_id: Optional[str]
    profile_id: Optional[str]
    artifacts: List[str]
    env_keys: List[str] = Field(default_factory=list)

    class Config:
        json_encoders = {Path: lambda v: str(v) if v else None}


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

    @app.get("/api/v1/profiles", response_model=ProfilesResponse)
    async def get_profiles() -> ProfilesResponse:
        return ProfilesResponse(profiles=profiles_config)

    @app.get("/api/v1/docs", response_model=DocsResponse)
    async def list_docs_route() -> DocsResponse:
        docs = scan_documents(settings, job_manager)
        return DocsResponse(items=docs)

    @app.get("/api/v1/docs/{doc_name}", response_model=DocDetailResponse)
    async def get_doc(doc_name: str) -> DocDetailResponse:
        doc_paths = _get_doc_paths(doc_name)
        doc_info = build_doc_info(settings, job_manager, doc_paths)
        return DocDetailResponse(doc=doc_info)

    @app.get("/api/v1/docs/{doc_name}/files", response_model=FilesResponse)
    async def doc_files(doc_name: str) -> FilesResponse:
        doc_paths = _get_doc_paths(doc_name)
        return FilesResponse(files=list_key_files(doc_paths))

    @app.get("/api/v1/docs/{doc_name}/suggested", response_model=SuggestedResponse)
    async def doc_suggested(doc_name: str) -> SuggestedResponse:
        doc_paths = _get_doc_paths(doc_name)
        suggested = doc_paths.suggested_glossary
        rules = load_rules(suggested) if suggested and suggested.exists() else []
        etag = compute_etag(suggested) if suggested else None
        return SuggestedResponse(rules=rules, etag=etag)

    @app.put("/api/v1/docs/{doc_name}/validated")
    async def save_validated(doc_name: str, payload: GlossaryPayload) -> APIResponse:
        doc_paths = _get_doc_paths(doc_name)
        if not doc_paths.work_dir:
            raise HTTPException(status_code=400, detail="work_dir introuvable")
        validated_path = doc_paths.work_dir / "rag.glossary.yaml"
        doc_id = payload.doc_id or doc_paths.doc_id
        current_etag = compute_etag(doc_paths.suggested_glossary)
        if payload.etag and payload.etag != (current_etag or ""):
            raise HTTPException(status_code=409, detail="Glossaire suggéré modifié. Rafraîchir.")
        try:
            save_validated_glossary(
                validated_path,
                doc_id,
                payload.rules,
                expected_etag=payload.etag,
                current_etag=current_etag,
            )
        except GlossaryValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return APIResponse()

    @app.get("/api/v1/docs/{doc_name}/preview", response_model=PreviewResponse)
    async def doc_preview(
        doc_name: str, pattern: Optional[str] = None, replacement: Optional[str] = None
    ) -> PreviewResponse:
        doc_paths = _get_doc_paths(doc_name)
        source = load_preview_text(doc_paths)
        result = await preview_with_timeout(
            source,
            pattern,
            replacement,
            settings.preview_timeout_ms,
        )
        return PreviewResponse(preview=result)

    @app.post("/api/v1/run/asr-batch", response_model=JobResponse)
    async def run_asr_batch(payload: AsrBatchRequest) -> JobResponse:
        return _schedule(lambda: build_asr_batch_command(settings, profiles_config, profile=payload.profile))

    @app.post("/api/v1/run/asr-batch/dry-run", response_model=CommandPreviewResponse)
    async def run_asr_batch_preview(payload: AsrBatchRequest) -> CommandPreviewResponse:
        return _preview(lambda: build_asr_batch_command(settings, profiles_config, profile=payload.profile))

    @app.post("/api/v1/run/lexicon-batch", response_model=JobResponse)
    async def run_lexicon_batch(payload: LexiconBatchRequest) -> JobResponse:
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

    @app.post("/api/v1/run/lexicon-batch/dry-run", response_model=CommandPreviewResponse)
    async def run_lexicon_batch_preview(payload: LexiconBatchRequest) -> CommandPreviewResponse:
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

    @app.post("/api/v1/run/rag-batch", response_model=JobResponse)
    async def run_rag_batch(payload: RagBatchRequest) -> JobResponse:
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

    @app.post("/api/v1/run/rag-batch/dry-run", response_model=CommandPreviewResponse)
    async def run_rag_batch_preview(payload: RagBatchRequest) -> CommandPreviewResponse:
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

    @app.post("/api/v1/run/lexicon-scan", response_model=JobResponse)
    async def run_lexicon_scan(payload: LexiconDocRequest) -> JobResponse:
        return _schedule(
            lambda: build_lexicon_scan_command(
                settings, payload.doc, profile=payload.profile, profiles=profiles_config
            )
        )

    @app.post("/api/v1/run/lexicon-scan/dry-run", response_model=CommandPreviewResponse)
    async def run_lexicon_scan_preview(payload: LexiconDocRequest) -> CommandPreviewResponse:
        return _preview(
            lambda: build_lexicon_scan_command(
                settings, payload.doc, profile=payload.profile, profiles=profiles_config
            )
        )

    @app.post("/api/v1/run/lexicon-apply", response_model=JobResponse)
    async def run_lexicon_apply(payload: LexiconDocRequest) -> JobResponse:
        return _schedule(
            lambda: build_lexicon_apply_command(
                settings, payload.doc, profiles=profiles_config, profile=payload.profile
            )
        )

    @app.post("/api/v1/run/lexicon-apply/dry-run", response_model=CommandPreviewResponse)
    async def run_lexicon_apply_preview(payload: LexiconDocRequest) -> CommandPreviewResponse:
        return _preview(
            lambda: build_lexicon_apply_command(
                settings, payload.doc, profiles=profiles_config, profile=payload.profile
            )
        )

    @app.post("/api/v1/run/rag-export", response_model=JobResponse)
    async def run_rag_export(payload: RagExportRequest) -> JobResponse:
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

    @app.post("/api/v1/run/rag-export/dry-run", response_model=CommandPreviewResponse)
    async def run_rag_export_preview(payload: RagExportRequest) -> CommandPreviewResponse:
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

    @app.post("/api/v1/run/rag-doctor", response_model=JobResponse)
    async def run_rag_doctor(payload: RagDoctorRequest) -> JobResponse:
        return _schedule(
            lambda: build_rag_doctor_command(
                settings,
                profiles_config,
                payload.doc,
                version_tag=payload.version_tag,
                profile=payload.profile,
            )
        )

    @app.post("/api/v1/run/rag-doctor/dry-run", response_model=CommandPreviewResponse)
    async def run_rag_doctor_preview(payload: RagDoctorRequest) -> CommandPreviewResponse:
        return _preview(
            lambda: build_rag_doctor_command(
                settings,
                profiles_config,
                payload.doc,
                version_tag=payload.version_tag,
                profile=payload.profile,
            )
        )

    @app.post("/api/v1/run/rag-query", response_model=JobResponse)
    async def run_rag_query(payload: RagQueryRequest) -> JobResponse:
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

    @app.post("/api/v1/run/rag-query/dry-run", response_model=CommandPreviewResponse)
    async def run_rag_query_preview(payload: RagQueryRequest) -> CommandPreviewResponse:
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

    @app.get("/api/v1/jobs", response_model=JobsResponse)
    async def list_jobs(limit: int = 100) -> JobsResponse:
        jobs = job_manager.list_jobs(limit=limit)
        return JobsResponse(jobs=jobs)

    @app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
    async def job_detail(job_id: int) -> JobResponse:
        job = job_manager.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job introuvable")
        return JobResponse(job=job)

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=CancelResponse)
    async def cancel_job(job_id: int) -> CancelResponse:
        if not await job_manager.cancel_job(job_id):
            raise HTTPException(status_code=409, detail="Impossible d'annuler ce job")
        return CancelResponse(canceled=True)

    @app.get("/api/v1/jobs/{job_id}/log", response_model=LogResponse)
    async def job_log(job_id: int) -> LogResponse:
        log = job_manager.read_log(job_id)
        return LogResponse(job_id=job_id, log=log)

    @app.get("/api/v1/jobs/{job_id}/log/file")
    async def job_log_download(job_id: int):
        path = job_manager.log_file_path(job_id)
        if not path:
            raise HTTPException(status_code=404, detail="Log introuvable")
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


def _build_job_payload(builder: Callable[[], JobCreate]) -> JobCreate:
    try:
        return builder()
    except CommandError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DocBusyError as exc:
        detail = {"reason": "doc_busy", "doc_id": exc.doc_id, "job_id": exc.job_id}
        raise HTTPException(status_code=409, detail=detail) from exc


def _schedule(builder: Callable[[], JobCreate]) -> JobResponse:
    payload = _build_job_payload(builder)
    job = job_manager.create_job(payload)
    job_manager.schedule(job.id)
    return JobResponse(job=job)


def _preview(builder: Callable[[], JobCreate]) -> CommandPreviewResponse:
    payload = _build_job_payload(builder)
    return CommandPreviewResponse(
        action=payload.action,
        argv=payload.argv,
        cwd=payload.cwd,
        doc_id=payload.doc_id,
        profile_id=payload.profile_id,
        artifacts=payload.artifacts,
    )


def _get_doc_paths(doc_name: str) -> DocPaths:
    try:
        return resolve_doc(settings, doc_name)
    except ResolverError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


app = create_app()

import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from control_room.backend import app as backend_app
from control_room.backend import preview as preview_module
from control_room.backend.docs import GlossaryValidationError, build_doc_info, save_validated_glossary
from control_room.backend.errors import FailureReason, classify_failure_from_log
from control_room.backend.exceptions import DocBusyError
from control_room.backend.jobs import JobAction, JobCreate
from control_room.backend.preview import preview_with_timeout
from control_room.backend.resolver import ResolverError, resolve_doc
from control_room.backend.runner import JobManager, JobStore


class DummySettings:
    def __init__(self, base: Path):
        self.base = base
        self.data_pipeline_root = base / "data"
        self.ts_repo_root = base / "repo"
        self.ts_run_bat_path = self.ts_repo_root / "bin" / "run.bat"
        self.ts_venv_dir = base / "venv"
        self.jobs_db_path = base / "jobs.db"
        self.logs_dir = base / "logs"
        self.frontend_dist_dir = base / "frontend"
        self.api_key = None
        self.max_workers = 2
        self.job_version = 1
        self.preview_timeout_ms = 200
        self.profiles_path = base / "profiles.yaml"

    @property
    def run_bat_path(self) -> Path:
        return self.ts_run_bat_path

    @property
    def asr_staging_dir(self) -> Path:
        return self.data_pipeline_root / "02_output_source" / "asr"

    @property
    def rag_output_dir(self) -> Path:
        return self.data_pipeline_root / "03_output_RAG"


def make_settings(tmp_path: Path) -> DummySettings:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "bin").mkdir()
    data_root = tmp_path / "data"
    (data_root / "02_output_source" / "asr").mkdir(parents=True)
    (data_root / "03_output_RAG").mkdir(parents=True)
    (tmp_path / "logs").mkdir(exist_ok=True)
    (tmp_path / "profiles.yaml").write_text("version: 1\nlexicon: {}\nrag: {}\nasr: {}\n", encoding="utf-8")
    return DummySettings(tmp_path)


def test_resolver_validates_doc_id(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    doc_root = settings.asr_staging_dir / "My Doc"
    (doc_root / "work" / "My Doc").mkdir(parents=True)
    resolved = resolve_doc(settings, "My Doc")
    assert resolved.work_dir and resolved.work_dir.name == "My Doc"
    with pytest.raises(ResolverError):
        resolve_doc(settings, "../evil")
    with pytest.raises(ResolverError):
        resolve_doc(settings, "bad/name")


def test_doc_state_rag_ready(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    doc_root = settings.asr_staging_dir / "DocA"
    work_dir = doc_root / "work" / "DocA"
    transcript_dir = doc_root / "TRANSCRIPT - DocA"
    work_dir.mkdir(parents=True)
    transcript_dir.mkdir()
    (work_dir / "rag.glossary.yaml").write_text("rules: []", encoding="utf-8")
    rag_dir = settings.rag_output_dir / "RAG-DocA" / "v1"
    rag_dir.mkdir(parents=True)
    manager = JobManager(settings)
    doc_paths = resolve_doc(settings, "DocA")
    info = build_doc_info(settings, manager, doc_paths)
    assert info.doc_state == "RAG_READY"
    assert info.rag_ready is True
    assert info.last_rag_version == "v1"


def test_doc_lock_serializes(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = JobManager(settings)
    order: list[str] = []

    async def worker(name: str) -> None:
        lock = manager._doc_locks["doc-1"]  # pylint: disable=protected-access
        async with lock:
            order.append(f"enter-{name}")
            await asyncio.sleep(0.01)
            order.append(f"exit-{name}")

    async def run() -> None:
        await asyncio.gather(worker("A"), worker("B"))

    asyncio.run(run())
    assert order in (
        ["enter-A", "exit-A", "enter-B", "exit-B"],
        ["enter-B", "exit-B", "enter-A", "exit-A"],
    )


def test_job_store_persistence(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    store = JobStore(tmp_path / "jobs.db")
    payload = JobCreate(
        action=JobAction.LEXICON_SCAN,
        argv=["echo", "ok"],
        cwd=tmp_path,
        doc_id="doc-1",
        profile_id="default",
        write_lock=True,
        job_version=7,
        artifacts=[],
    )
    job_id = store.insert_job(payload, logs_dir / "job.log")
    record = store.get_job(job_id)
    assert record
    assert record.job_version == 7
    assert record.write_lock is True
    assert record.action == JobAction.LEXICON_SCAN


def test_preview_timeout(monkeypatch) -> None:
    def slow_preview(text, pattern, replacement):
        time.sleep(0.2)
        return text, 0

    monkeypatch.setattr(preview_module, "_run_regex_preview", slow_preview)
    text = "dummy"
    result = asyncio.run(preview_with_timeout(text, ".*", "b", timeout_ms=50))
    assert result["error"] == "Regex timeout"


def test_glossary_validation(tmp_path: Path) -> None:
    target = tmp_path / "rag.glossary.yaml"
    with pytest.raises(GlossaryValidationError):
        save_validated_glossary(
            target,
            "doc",
            [
                {"pattern": "(", "replacement": "x"},
            ],
            expected_etag=None,
            current_etag=None,
        )
    save_validated_glossary(
        target,
        "doc",
        [{"pattern": "\\bword\\b", "replacement": "mot"}],
        expected_etag=None,
        current_etag=None,
    )
    assert target.exists()
    assert "doc" in target.read_text(encoding="utf-8")


def test_failure_reason_mapping() -> None:
    reason, hint = classify_failure_from_log("pyannote_token missing", exit_code=1, canceled=False)
    assert reason == FailureReason.PYANNOTE_TOKEN_MISSING
    assert hint
    reason, _ = classify_failure_from_log("some text", exit_code=1, canceled=False)
    assert reason == FailureReason.UNKNOWN
    reason, _ = classify_failure_from_log("", exit_code=0, canceled=True)
    assert reason == FailureReason.CANCELED


def test_api_version_in_docs(monkeypatch) -> None:
    monkeypatch.setattr(backend_app, "scan_documents", lambda *args, **kwargs: [])
    client = TestClient(backend_app.app)
    response = client.get("/api/v1/docs")
    assert response.status_code == 200
    data = response.json()
    assert data["api_version"] == "v1"
    assert data["error"] is None
    assert data["data"] == {"docs": []}


def test_read_only_job_rejected_when_locked(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    manager = JobManager(settings)
    manager._doc_lock_owner["doc-lock"] = 42  # pylint: disable=protected-access
    read_job = JobCreate(
        action=JobAction.RAG_DOCTOR,
        argv=["cmd"],
        cwd=tmp_path,
        doc_id="doc-lock",
        profile_id=None,
        write_lock=False,
        job_version=1,
        artifacts=[],
    )
    with pytest.raises(DocBusyError):
        manager.create_job(read_job)


def test_profiles_endpoint_contract() -> None:
    client = TestClient(backend_app.app)
    response = client.get("/api/v1/profiles")
    assert response.status_code == 200
    payload = response.json()
    assert payload["api_version"] == "v1"
    assert payload["error"] is None
    assert "profiles" in payload["data"]


def test_jobs_endpoint_contract(monkeypatch) -> None:
    class DummyManager:
        def list_jobs(self, limit: int = 100):
            return []

    monkeypatch.setattr(backend_app, "job_manager", DummyManager())
    client = TestClient(backend_app.app)
    response = client.get("/api/v1/jobs")
    assert response.status_code == 200
    payload = response.json()
    assert payload["api_version"] == "v1"
    assert payload["error"] is None
    assert payload["data"] == {"jobs": []}

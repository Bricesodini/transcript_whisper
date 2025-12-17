from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .jobs import JobAction, JobCreate
from .profiles import ProfilesConfig, ProfileEntry
from .resolver import DocPaths, resolve_doc
from .settings import Settings


class CommandError(ValueError):
    pass


def _ensure_script(path: Path) -> Path:
    if not path.exists():
        raise CommandError(f"Script introuvable: {path}")
    return path


def _wrap_batch(script: Path, args: List[str]) -> List[str]:
    _ensure_script(script)
    return ["cmd.exe", "/c", str(script), *args]


def _profile_args(group: Dict[str, ProfileEntry], profile: Optional[str]) -> List[str]:
    if not profile:
        return []
    entry = group.get(profile)
    if not entry:
        raise CommandError(f"Profil inconnu: {profile}")
    return list(entry.args)


def build_asr_batch_command(
    settings: Settings, profiles: ProfilesConfig, *, profile: Optional[str]
) -> JobCreate:
    script = settings.ts_repo_root / "bin" / "pipeline_asr_batch.bat"
    args = _profile_args(profiles.asr, profile)
    return JobCreate(
        action=JobAction.ASR_BATCH,
        argv=_wrap_batch(script, args),
        cwd=settings.ts_repo_root,
        doc_id=None,
        profile_id=profile,
        write_lock=True,
        job_version=settings.job_version,
        artifacts=[],
    )


def build_lexicon_batch_command(
    settings: Settings,
    profiles: ProfilesConfig,
    *,
    force: bool,
    apply: bool,
    scan_only: bool,
    doc: Optional[str],
    max_docs: Optional[int],
    profile: Optional[str],
) -> JobCreate:
    script = settings.ts_repo_root / "bin" / "pipeline_lexicon_batch.bat"
    args: List[str] = []
    args.extend(_profile_args(profiles.lexicon, profile))
    if force:
        args.append("--force")
    if apply:
        args.append("--apply")
    elif scan_only:
        args.append("--scan-only")
    if doc:
        args.extend(["--doc", doc])
    if max_docs:
        args.extend(["--max-docs", str(max_docs)])
    return JobCreate(
        action=JobAction.LEXICON_BATCH,
        argv=_wrap_batch(script, args),
        cwd=settings.ts_repo_root,
        doc_id=doc,
        profile_id=profile,
        write_lock=True,
        job_version=settings.job_version,
        artifacts=[],
    )


def build_rag_batch_command(
    settings: Settings,
    profiles: ProfilesConfig,
    *,
    doc: Optional[str],
    version_tag: Optional[str],
    query: Optional[str],
    force: bool,
    profile: Optional[str],
) -> JobCreate:
    script = settings.ts_repo_root / "bin" / "pipeline_rag_batch.bat"
    args: List[str] = []
    args.extend(_profile_args(profiles.rag, profile))
    if doc:
        args.extend(["--doc", doc])
    if version_tag:
        args.extend(["--version-tag", version_tag])
    if query:
        args.extend(["--query", query])
    if force:
        args.append("--force")
    return JobCreate(
        action=JobAction.RAG_BATCH,
        argv=_wrap_batch(script, args),
        cwd=settings.ts_repo_root,
        doc_id=doc,
        profile_id=profile,
        write_lock=True,
        job_version=settings.job_version,
        artifacts=[],
    )


def _resolve_work_dir(doc_paths: DocPaths) -> Path:
    if not doc_paths.work_dir:
        raise CommandError("work_dir introuvable pour ce document.")
    return doc_paths.work_dir


def build_lexicon_scan_command(
    settings: Settings, doc_id: str, *, profile: Optional[str], profiles: ProfilesConfig
) -> JobCreate:
    doc_paths = resolve_doc(settings, doc_id)
    work_dir = _resolve_work_dir(doc_paths)
    args = ["rag", "lexicon", "scan", "--input", str(work_dir)]
    args.extend(_profile_args(profiles.lexicon, profile))
    script = settings.run_bat_path
    return JobCreate(
        action=JobAction.LEXICON_SCAN,
        argv=_wrap_batch(script, args),
        cwd=settings.ts_repo_root,
        doc_id=doc_paths.doc_id,
        profile_id=profile,
        write_lock=True,
        job_version=settings.job_version,
        artifacts=[str(work_dir / "rag.glossary.suggested.yaml")],
    )


def build_lexicon_apply_command(
    settings: Settings, doc_id: str, *, profiles: ProfilesConfig, profile: Optional[str]
) -> JobCreate:
    doc_paths = resolve_doc(settings, doc_id)
    work_dir = _resolve_work_dir(doc_paths)
    args = ["rag", "lexicon", "apply", "--input", str(work_dir)]
    args.extend(_profile_args(profiles.lexicon, profile))
    script = settings.run_bat_path
    return JobCreate(
        action=JobAction.LEXICON_APPLY,
        argv=_wrap_batch(script, args),
        cwd=settings.ts_repo_root,
        doc_id=doc_paths.doc_id,
        profile_id=profile,
        write_lock=True,
        job_version=settings.job_version,
        artifacts=[
            str(work_dir / "rag.glossary.yaml"),
            str(work_dir / ".lexicon_ok.json"),
        ],
    )


def build_rag_export_command(
    settings: Settings,
    profiles: ProfilesConfig,
    doc_id: str,
    *,
    version_tag: Optional[str],
    force: bool,
    profile: Optional[str],
) -> JobCreate:
    doc_paths = resolve_doc(settings, doc_id)
    work_dir = _resolve_work_dir(doc_paths)
    args = ["rag", "--input", str(work_dir)]
    args.extend(_profile_args(profiles.rag, profile))
    if force:
        args.append("--force")
    if version_tag:
        args.extend(["--version-tag", version_tag])
    script = settings.run_bat_path
    rag_dir = settings.rag_output_dir / f"RAG-{doc_paths.doc_id}"
    return JobCreate(
        action=JobAction.RAG_EXPORT,
        argv=_wrap_batch(script, args),
        cwd=settings.ts_repo_root,
        doc_id=doc_paths.doc_id,
        profile_id=profile,
        write_lock=True,
        job_version=settings.job_version,
        artifacts=[str(rag_dir)],
    )


def build_rag_doctor_command(
    settings: Settings,
    profiles: ProfilesConfig,
    doc_id: str,
    *,
    version_tag: Optional[str],
    profile: Optional[str],
) -> JobCreate:
    doc_paths = resolve_doc(settings, doc_id)
    rag_input = _resolve_rag_input(settings, doc_paths.doc_id, version_tag)
    args = ["rag", "doctor", "--input", str(rag_input)]
    args.extend(_profile_args(profiles.rag, profile))
    script = settings.run_bat_path
    return JobCreate(
        action=JobAction.RAG_DOCTOR,
        argv=_wrap_batch(script, args),
        cwd=settings.ts_repo_root,
        doc_id=doc_paths.doc_id,
        profile_id=profile,
        write_lock=False,
        job_version=settings.job_version,
        artifacts=[str(rag_input)],
    )


def build_rag_query_command(
    settings: Settings,
    profiles: ProfilesConfig,
    doc_id: str,
    *,
    query: str,
    version_tag: Optional[str],
    top_k: Optional[int],
    profile: Optional[str],
) -> JobCreate:
    if not query.strip():
        raise CommandError("Query vide.")
    doc_paths = resolve_doc(settings, doc_id)
    rag_input = _resolve_rag_input(settings, doc_paths.doc_id, version_tag)
    args = [
        "rag",
        "query",
        "--input",
        str(rag_input),
        "--query",
        query.strip(),
    ]
    if top_k:
        args.extend(["--top-k", str(top_k)])
    args.extend(_profile_args(profiles.rag, profile))
    script = settings.run_bat_path
    return JobCreate(
        action=JobAction.RAG_QUERY,
        argv=_wrap_batch(script, args),
        cwd=settings.ts_repo_root,
        doc_id=doc_paths.doc_id,
        profile_id=profile,
        write_lock=False,
        job_version=settings.job_version,
        artifacts=[str(rag_input)],
    )


def _resolve_rag_input(settings: Settings, doc_id: str, version_tag: Optional[str]) -> Path:
    base = settings.rag_output_dir / f"RAG-{doc_id}"
    if not base.exists():
        raise CommandError("Aucun export RAG trouvé pour ce document.")
    if version_tag:
        target = base / version_tag
        if not target.exists():
            raise CommandError(f"Version RAG inconnue: {version_tag}")
        return target
    candidates = sorted([child for child in base.iterdir() if child.is_dir()], reverse=True)
    if not candidates:
        raise CommandError("Aucune version RAG disponible.")
    return candidates[0]

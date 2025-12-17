from __future__ import annotations

from pathlib import Path

from datetime import datetime, timezone

from tools import nas_audit, stage_cleanup


def test_build_audit_handles_orphans(tmp_path: Path) -> None:
    root = tmp_path / "nas"
    (root / "02_output_source" / "asr" / "DocA").mkdir(parents=True)
    (root / "03_output_RAG" / "RAG-DocB").mkdir(parents=True)
    payload = nas_audit.build_audit(root)
    assert payload["root"] == str(root)
    assert "directories" in payload
    assert "DocB" in payload["orphans"]["missing_source"]
    assert "DocA" in payload["orphans"]["missing_rag"]


def test_archive_docs_moves_with_apply(tmp_path: Path) -> None:
    root = tmp_path / "nas"
    src = root / "02_output_source" / "asr" / "Doc"
    src.mkdir(parents=True)
    moves = nas_audit.archive_docs(root, ["Doc"], dry_run=True)
    assert moves
    assert not (root / "04_archive").exists()
    nas_audit.archive_docs(root, ["Doc"], dry_run=False)
    archived = root / "04_archive" / "02_output_source" / "asr" / "Doc"
    assert archived.exists()


def test_nas_audit_outputs(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "nas"
    root.mkdir()
    fixed = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(nas_audit, "_now", lambda: fixed)
    report, json_path, stamp = nas_audit.resolve_output_paths(
        root,
        write_docs=False,
        report="docs/NAS_AUDIT.md",
        json_path="docs/NAS_AUDIT.json",
        out_dir="logs",
    )
    assert stamp == "20250101_120000"
    assert report == root / "logs" / "nas_audit_20250101_120000.md"
    assert json_path == root / "logs" / "nas_audit_20250101_120000.json"


def test_stage_path_builder(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    stage = stage_cleanup.build_stage_path(repo, "20250101_120000")
    assert stage == repo.parent / "repo__cleanup_stage_20250101_120000"

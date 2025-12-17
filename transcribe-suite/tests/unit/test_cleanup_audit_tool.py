from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from tools import cleanup_audit


def test_collect_references_detects_keywords(tmp_path: Path) -> None:
    project = tmp_path / "transcribe-suite"
    project.mkdir(parents=True)
    sample = project / "example.py"
    sample.write_text("exports_dir = 'exports/legacy'\n", encoding="utf-8")
    files = [sample]
    refs = cleanup_audit.collect_references(files, cleanup_audit.KEYWORDS)
    assert any(ref.pattern == "exports_dir" for ref in refs)


def test_specific_keyword_precedes_generic(tmp_path: Path) -> None:
    project = tmp_path / "transcribe-suite"
    project.mkdir(parents=True)
    sample = project / "example.py"
    sample.write_text("exports_dir = exports\n", encoding="utf-8")
    refs = cleanup_audit.collect_references([sample], cleanup_audit.KEYWORDS)
    assert refs
    assert refs[0].pattern == "exports_dir"


def test_build_report_lists_directories(tmp_path: Path) -> None:
    project = tmp_path / "transcribe-suite"
    (project / "inputs").mkdir(parents=True)
    dirs = cleanup_audit.audit_directories(project)
    report = cleanup_audit.build_report(project, dirs, [])
    assert "Dossiers historiques" in report
    assert "inputs" in report


def test_inventory_and_action_plan(tmp_path: Path) -> None:
    project = tmp_path / "transcribe-suite"
    (project / "exports").mkdir(parents=True)
    dirs = cleanup_audit.audit_directories(project)
    inventory = cleanup_audit.build_inventory(project)
    assert any(entry.classification == "legacy" for entry in inventory)
    actions = cleanup_audit.determine_actions(project, dirs)
    assert actions and actions[0]["source"].endswith("exports")
    target = cleanup_audit.next_deprecated_target(project, "exports")
    assert target.name == "_deprecated_exports"
    target.touch()
    target2 = cleanup_audit.next_deprecated_target(project, "exports")
    assert target2.name == "_deprecated_exports_2"


def test_rename_supports_dry_run(tmp_path: Path) -> None:
    project = tmp_path / "transcribe-suite"
    (project / "work").mkdir(parents=True)
    dirs = cleanup_audit.audit_directories(project)
    actions = cleanup_audit.determine_actions(project, dirs)
    logs: list[str] = []
    cleanup_audit.rename_legacy_dirs(actions, dry_run=True, logger=logs.append)
    assert (project / "_deprecated_work").exists() is False
    cleanup_audit.rename_legacy_dirs(actions, dry_run=False, logger=logs.append)
    assert (project / "_deprecated_work").exists()
    readme = project / "_deprecated_work" / "README.md"
    assert readme.exists()


def test_resolve_outputs_logs(tmp_path: Path, monkeypatch) -> None:
    repo = (tmp_path / "repo_pkg")
    repo.mkdir()
    fixed = datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(cleanup_audit, "_now", lambda: fixed)
    report, plan, stamp = cleanup_audit.resolve_output_paths(
        repo,
        write_docs=False,
        write_report="docs/CLEANUP_AUDIT.md",
        write_plan="docs/CLEANUP_PLAN.json",
        out_dir="logs",
    )
    assert stamp == "20250102_030405"
    assert report == repo.parent / "logs" / "cleanup_audit_20250102_030405.md"
    assert plan == repo.parent / "logs" / "cleanup_plan_20250102_030405.json"


def test_resolve_outputs_docs(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(cleanup_audit, "_now", lambda: datetime(2025, 2, 3, tzinfo=timezone.utc))
    report, plan, _ = cleanup_audit.resolve_output_paths(
        repo,
        write_docs=True,
        write_report="docs/CLEANUP_AUDIT.md",
        write_plan="docs/CLEANUP_PLAN.json",
        out_dir="logs",
    )
    assert report == repo.parent / "docs" / "CLEANUP_AUDIT.md"
    assert plan == repo.parent / "docs" / "CLEANUP_PLAN.json"

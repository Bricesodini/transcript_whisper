import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from rag_export.doc_id import compute_doc_id
from rag_export import RAG_SCHEMA_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "rag_sample"


def _build_temp_config(tmp_path: Path):
    output_root = tmp_path / "rag-out"
    log_root = tmp_path / "rag-logs"
    cfg_path = tmp_path / "rag.yaml"
    cfg_text = f"""
rag:
  schema_version: "0.1.0"
  output_dir: "{output_root.as_posix()}"
  export_dir_prefix: "RAG-"
  default_lang: auto
  doc_id:
    slug_max_length: 72
    hash_length: 8
    fallback_hash_length: 12
  chunks:
    target_tokens: 200
    overlap_tokens: 40
    llm_chunks_enabled: false
  quality:
    threshold_warn: 0.6
    threshold_error: 0.45
  index:
    enable_sqlite: true
    enable_embeddings: false
  citations:
    base_url:
    url_template: "{{base_url}}?t={{start_s}}s"
    text_format: "{{title}} [{{start_mmss}}-{{end_mmss}}]"
    markdown_format: "[Voir extrait]({{url}}) ({{start_mmss}}-{{end_mmss}})"
  health:
    coverage_target_pct: 0.95
    sample_queries: []
  versioning:
    allow_force: true
    default_strategy: "error"
    version_tag_format: "{{doc_id}}-{{timestamp}}"
  logging:
    diff_stats: true
    log_dir: "{log_root.as_posix()}"
"""
    cfg_path.write_text(cfg_text.strip(), encoding="utf-8")
    return cfg_path, output_root


def _run_cli(args, *, cwd=PROJECT_ROOT, check: bool = True):
    cmd = [sys.executable, "-m", "rag_export.cli"]
    cmd.extend(args)
    env = os.environ.copy()
    src_path = str((PROJECT_ROOT / "src").resolve())
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = src_path if not existing else f"{src_path}{os.pathsep}{existing}"
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    if check and result.returncode != 0:
        raise AssertionError(f"rag-export failed ({result.returncode}): {result.stderr}\n{result.stdout}")
    return result


def _prepare_export(tmp_path):
    cfg_path, output_root = _build_temp_config(tmp_path)
    work_dir = FIXTURE_ROOT / "work" / "sample_doc"
    doc_id = compute_doc_id("sample_doc", str(work_dir))
    target_dir = output_root / f"RAG-{doc_id}" / RAG_SCHEMA_VERSION
    return cfg_path, output_root, work_dir, doc_id, target_dir


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_rag_export_dry_run(tmp_path):
    cfg_path, output_root = _build_temp_config(tmp_path)
    transcript_dir = FIXTURE_ROOT / "transcripts" / "TRANSCRIPT - sample_doc"
    _run_cli(["--input", str(transcript_dir), "--config", str(cfg_path), "--dry-run"])
    assert not any(output_root.glob("*")), "dry-run should not emit artefacts"


def test_rag_export_generates_artifacts(tmp_path):
    cfg_path, output_root, work_dir, doc_id, target_dir = _prepare_export(tmp_path)
    _run_cli(["--input", str(work_dir), "--config", str(cfg_path)])
    assert target_dir.exists()

    manifest_path = target_dir / "document.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["doc_id"] == doc_id
    assert manifest["stats"]["nb_segments"] == 3

    segments_lines = (target_dir / "segments.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(segments_lines) == 3

    chunks_lines = (target_dir / "chunks.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(chunks_lines) >= 1

    assert (target_dir / "quality.json").exists()
    assert (target_dir / "README_RAG.md").exists()
    assert (target_dir / "lexical.sqlite").exists()


def test_rag_export_idempotent(tmp_path):
    cfg_path, output_root, work_dir, _, target_dir = _prepare_export(tmp_path)
    base_args = ["--input", str(work_dir), "--config", str(cfg_path), "--force"]
    _run_cli(base_args)
    files = ["document.json", "segments.jsonl", "chunks.jsonl", "quality.json", "README_RAG.md", "lexical.sqlite"]
    first_run = {name: (target_dir / name).read_bytes() for name in files}
    _run_cli(base_args)
    for name in files:
        assert (target_dir / name).read_bytes() == first_run[name], f"{name} differs between runs"


def test_provenance_manifest(tmp_path):
    cfg_path, _, work_dir, doc_id, target_dir = _prepare_export(tmp_path)
    _run_cli(["--input", str(work_dir), "--config", str(cfg_path)])
    manifest = json.loads((target_dir / "document.json").read_text(encoding="utf-8"))
    provenance = manifest["provenance"]
    segments_path = PROJECT_ROOT / provenance["segments"]["path"]
    clean_path = PROJECT_ROOT / provenance["clean_text"]["path"]
    metrics_path = PROJECT_ROOT / provenance["metrics"]["path"]
    assert provenance["segments"]["sha256"] == _sha256(segments_path)
    assert provenance["clean_text"]["sha256"] == _sha256(clean_path)
    assert provenance["metrics"]["sha256"] == _sha256(metrics_path)
    effective_path = target_dir / "config.effective.yaml"
    assert effective_path.exists()
    assert manifest["config_effective_sha256"] == _sha256(effective_path)
    assert manifest["generated_at"] == "1970-01-01T00:00:00Z"
    assert manifest["deterministic_mode"] is True
    assert manifest["timestamps_policy"] == "epoch"


def test_rag_doctor_ok(tmp_path):
    cfg_path, _, work_dir, _, target_dir = _prepare_export(tmp_path)
    _run_cli(["--input", str(work_dir), "--config", str(cfg_path)])
    _run_cli(["doctor", "--input", str(target_dir), "--config", str(cfg_path)])


def test_rag_doctor_missing_file(tmp_path):
    cfg_path, _, work_dir, _, target_dir = _prepare_export(tmp_path)
    _run_cli(["--input", str(work_dir), "--config", str(cfg_path)])
    (target_dir / "chunks.jsonl").unlink()
    result = _run_cli(["doctor", "--input", str(target_dir), "--config", str(cfg_path)], check=False)
    assert result.returncode != 0


def test_sqlite_fts_query_smoke(tmp_path):
    cfg_path, _, work_dir, _, target_dir = _prepare_export(tmp_path)
    _run_cli(["--input", str(work_dir), "--config", str(cfg_path)])
    db_path = target_dir / "lexical.sqlite"
    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT 1", ("installation",))
        assert cur.fetchone() is not None
    finally:
        conn.close()


def test_rag_query_cli(tmp_path):
    cfg_path, _, work_dir, _, target_dir = _prepare_export(tmp_path)
    _run_cli(["--input", str(work_dir), "--config", str(cfg_path)])
    result = _run_cli(
        ["query", "--input", str(target_dir), "--config", str(cfg_path), "--query", "installation", "--top-k", "2"]
    )
    assert "rag query" in result.stdout.lower()


def test_rag_query_missing_db(tmp_path):
    cfg_path, _, work_dir, _, target_dir = _prepare_export(tmp_path)
    _run_cli(["--input", str(work_dir), "--config", str(cfg_path)])
    (target_dir / "lexical.sqlite").unlink()
    result = _run_cli(
        ["query", "--input", str(target_dir), "--config", str(cfg_path), "--query", "installation"],
        check=False,
    )
    assert result.returncode != 0

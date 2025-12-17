import json
import shutil
from pathlib import Path

import yaml

from rag_export.doc_id import compute_doc_id

from tests.unit.test_rag_export_cli import FIXTURE_ROOT, _build_temp_config, _run_cli


def _copy_work_tree(tmp_path, doc_name="sample_doc_lexicon"):
    bundle = tmp_path / "bundle"
    work_src = FIXTURE_ROOT / "work" / doc_name
    transcript_src = FIXTURE_ROOT / "transcripts" / f"TRANSCRIPT - {doc_name}"
    work_dst = bundle / "work" / doc_name
    transcript_dst = bundle / f"TRANSCRIPT - {doc_name}"
    shutil.copytree(work_src, work_dst, dirs_exist_ok=True)
    shutil.copytree(transcript_src, transcript_dst, dirs_exist_ok=True)
    return work_dst, doc_name


def _read_embedding_rows(path: Path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def test_lexicon_scan_produces_yaml(tmp_path):
    cfg_path, _ = _build_temp_config(tmp_path)
    work_dir, doc_name = _copy_work_tree(tmp_path)
    _run_cli(
        [
            "lexicon",
            "scan",
            "--input",
            str(work_dir),
            "--config",
            str(cfg_path),
            "--min-count",
            "1",
            "--top-k",
            "25",
        ]
    )
    suggested = work_dir / "rag.glossary.suggested.yaml"
    assert suggested.exists()
    payload = yaml.safe_load(suggested.read_text(encoding="utf-8"))
    assert payload["doc_id"] == compute_doc_id(doc_name, str(work_dir))
    rules = payload["rules"]
    assert any("cloude" in rule["pattern"] for rule in rules)
    assert any("mod" in rule["pattern"] for rule in rules)
    assert any("galixy" in rule["pattern"] for rule in rules)


def test_lexicon_apply_creates_validated_file(tmp_path):
    cfg_path, _ = _build_temp_config(tmp_path)
    work_dir, doc_name = _copy_work_tree(tmp_path)
    _run_cli(
        [
            "lexicon",
            "scan",
            "--input",
            str(work_dir),
            "--config",
            str(cfg_path),
            "--min-count",
            "1",
        ]
    )
    _run_cli(
        [
            "lexicon",
            "apply",
            "--input",
            str(work_dir),
            "--config",
            str(cfg_path),
        ]
    )
    validated = work_dir / "rag.glossary.yaml"
    assert validated.exists()
    payload = yaml.safe_load(validated.read_text(encoding="utf-8"))
    assert len(payload["rules"]) >= 1
    stamp_path = work_dir / ".lexicon_ok.json"
    assert stamp_path.exists()
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    assert stamp["doc"] == compute_doc_id(doc_name, str(work_dir))
    assert stamp["source_file"] == "05_polished.json"
    assert stamp["rules_count"] == len(payload["rules"])


def test_rag_export_ignores_suggested_until_validated(tmp_path):
    cfg_path, output_root = _build_temp_config(tmp_path)
    work_dir, doc_name = _copy_work_tree(tmp_path)
    _run_cli(["lexicon", "scan", "--input", str(work_dir), "--config", str(cfg_path), "--min-count", "1"])
    _run_cli(["--input", str(work_dir), "--config", str(cfg_path), "--force"])
    doc_id = compute_doc_id(doc_name, str(work_dir))
    target_dir = output_root / f"RAG-{doc_id}" / "0.1.0"
    embed_path = target_dir / "chunks_for_embedding.jsonl"
    rows = _read_embedding_rows(embed_path)
    assert any("galixy" in row["text_norm"] for row in rows)
    _run_cli(
        [
            "lexicon",
            "apply",
            "--input",
            str(work_dir),
            "--config",
            str(cfg_path),
        ]
    )
    _run_cli(["--input", str(work_dir), "--config", str(cfg_path), "--force"])
    rows_after = _read_embedding_rows(embed_path)
    assert not any("galixy" in row["text_norm"] for row in rows_after)


def test_lexicon_stamp_updates_after_source_change(tmp_path):
    cfg_path, _ = _build_temp_config(tmp_path)
    work_dir, _ = _copy_work_tree(tmp_path)
    _run_cli(["lexicon", "scan", "--input", str(work_dir), "--config", str(cfg_path), "--min-count", "1"])
    _run_cli(["lexicon", "apply", "--input", str(work_dir), "--config", str(cfg_path)])
    stamp_path = work_dir / ".lexicon_ok.json"
    original = json.loads(stamp_path.read_text(encoding="utf-8"))
    polished = work_dir / "05_polished.json"
    polished.write_text(polished.read_text(encoding="utf-8") + "\nMOD", encoding="utf-8")
    _run_cli(["lexicon", "apply", "--input", str(work_dir), "--config", str(cfg_path)])
    updated = json.loads(stamp_path.read_text(encoding="utf-8"))
    assert updated["source_sha256"] != original["source_sha256"]


def test_rag_doctor_warns_on_pending_glossary(tmp_path):
    cfg_path, output_root = _build_temp_config(tmp_path)
    work_dir, doc_name = _copy_work_tree(tmp_path)
    _run_cli(["--input", str(work_dir), "--config", str(cfg_path)])
    doc_id = compute_doc_id(doc_name, str(work_dir))
    target_dir = output_root / f"RAG-{doc_id}" / "0.1.0"
    suggested = work_dir / "rag.glossary.suggested.yaml"
    suggested.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "doc_id": "demo",
                "rules": [{"pattern": "\\bfoo\\b", "replacement": "bar"}],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    result = _run_cli(["doctor", "--input", str(target_dir), "--config", str(cfg_path)], check=True)
    cfg_data = yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8"))
    log_dir_raw = cfg_data["rag"]["logging"]["log_dir"]
    log_dir = Path(log_dir_raw)
    if not log_dir.is_absolute():
        log_dir = (Path(cfg_path).parent / log_dir).resolve()
    log_path = log_dir / f"rag_doctor_{target_dir.name}.log"
    assert log_path.exists()
    log_text = log_path.read_text(encoding="utf-8").lower()
    assert "glossaire suggéré détecté" in log_text

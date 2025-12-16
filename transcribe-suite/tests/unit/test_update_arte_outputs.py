import json
from pathlib import Path

from tools.update_arte_outputs import refresh_arte_outputs


def test_refresh_arte_outputs_skips_when_word_scores_missing(tmp_path):
    work_dir = tmp_path / "work"
    export_dir = tmp_path / "exports"
    work_dir.mkdir()
    export_dir.mkdir()

    (work_dir / "05_polished.json").write_text(json.dumps({"segments": []}), encoding="utf-8")
    (work_dir / "structure.json").write_text(json.dumps({"sections": []}), encoding="utf-8")

    base_name = "demo"
    (export_dir / f"{base_name}.clean.jsonl").write_text("{}", encoding="utf-8")

    result = refresh_arte_outputs(work_dir, export_dir, doc_id=base_name)

    assert result["base_name"] == base_name
    assert result["status"] == "skipped"
    assert result["reason"] == "no_word_scores"

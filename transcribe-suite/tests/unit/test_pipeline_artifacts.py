import json
import sys
from types import SimpleNamespace

import pytest

# Provide a minimal yaml shim so importing pipeline does not require PyYAML in tests.
if "yaml" not in sys.modules:
    import types

    fake_yaml = types.SimpleNamespace(safe_load=lambda *args, **kwargs: {})
    sys.modules["yaml"] = fake_yaml

from pipeline import PipelineRunner
from utils import PipelineError


def _build_runner(tmp_path):
    runner = PipelineRunner.__new__(PipelineRunner)
    runner.command = "run"
    runner.strict = True
    media_parent = tmp_path / "media"
    media_parent.mkdir()
    runner.media_path = media_parent / "Demo Video.mp4"
    runner.media_path.write_text("", encoding="utf-8")

    runner.work_dir = tmp_path / "work" / runner.media_path.stem
    runner.work_dir.mkdir(parents=True, exist_ok=True)
    runner.audio_path = runner.work_dir / "audio_16k.wav"
    runner.audio_path.write_text("", encoding="utf-8")
    runner.manifest_path = runner.work_dir / "manifest.csv"
    runner.manifest_path.write_text("index,start,end\n", encoding="utf-8")

    for name in ("02_merged_raw.json", "03_aligned_whisperx.json", "04_cleaned.json", "05_polished.json"):
        (runner.work_dir / name).write_text(json.dumps({}), encoding="utf-8")
    for sub in ("00_segments", "01_asr_jsonl"):
        (runner.work_dir / sub).mkdir(exist_ok=True)

    runner.out_dir = tmp_path / "exports" / f"TRANSCRIPT - {runner.media_path.stem}"
    runner.out_dir.mkdir(parents=True, exist_ok=True)
    stem = runner.media_path.stem
    for fmt in ("md", "json", "vtt"):
        (runner.out_dir / f"{stem}.{fmt}").write_text("", encoding="utf-8")
    (runner.out_dir / f"{stem}.chapters.json").write_text(json.dumps({"sections": []}), encoding="utf-8")
    (runner.out_dir / f"{stem}.low_confidence.csv").write_text("", encoding="utf-8")

    runner.export_formats = ["md", "json", "vtt"]
    runner.exporter = SimpleNamespace(low_conf_csv_enabled=True, low_conf_csv_output=None)
    runner.structure_enabled = True
    runner.last_artifacts = {}
    return runner


def test_verify_artifacts_allows_extra_exports(tmp_path):
    runner = _build_runner(tmp_path)
    stem = runner.media_path.stem
    (runner.out_dir / f"{stem}.metrics.json").write_text("{}", encoding="utf-8")
    (runner.out_dir / f"{stem}.audit.md").write_text("", encoding="utf-8")

    runner._verify_artifacts()  # no exception despite extra files


def test_verify_artifacts_missing_export_fails(tmp_path):
    runner = _build_runner(tmp_path)
    missing = runner.out_dir / f"{runner.media_path.stem}.md"
    missing.unlink()

    with pytest.raises(PipelineError):
        runner._verify_artifacts()

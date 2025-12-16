import json
import logging
import sys
import time
from pathlib import Path

if "yaml" not in sys.modules:
    import types

    sys.modules["yaml"] = types.SimpleNamespace(safe_load=lambda *args, **kwargs: {})

from pipeline import PipelineRunner


def _bootstrap_runner(tmp_path: Path) -> PipelineRunner:
    runner = PipelineRunner.__new__(PipelineRunner)
    runner.media_path = tmp_path / "demo.mp4"
    runner.media_path.write_text("", encoding="utf-8")
    runner.input_hash = "abc123"
    runner.config_path = tmp_path / "config.yaml"
    runner.config_path.write_text("{}", encoding="utf-8")
    runner.strict = True
    runner.mode = "mono"
    runner.fail_fast = True
    runner.no_partial_export = True
    runner._run_start = time.time() - 5
    runner.run_stats = {"stages": {"export": 1.23}}
    runner.asr_metrics = {"segments": 42}
    runner.out_dir = tmp_path / "exports" / "TRANSCRIPT - demo"
    runner.out_dir.mkdir(parents=True, exist_ok=True)
    runner.last_artifacts = {"md": runner.out_dir / "demo.md"}
    runner.work_dir = tmp_path / "work"
    runner.work_dir.mkdir()
    runner.local_log_dir = runner.work_dir / "logs"
    runner.local_log_dir.mkdir()
    runner.logger = logging.getLogger("test-runner")
    runner._collect_versions = lambda: {"python": "3.x"}
    return runner


def test_finalize_run_writes_export_dir(tmp_path):
    runner = _bootstrap_runner(tmp_path)
    runner.finalize_run(success=True, error=None)
    manifest_path = runner.local_log_dir / "run_manifest.json"
    assert manifest_path.exists()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["export_dir"] == str(runner.out_dir)
    assert payload["status"] == "ok"
    assert payload["exports"] == ["md"]
    assert payload["export_paths"]["md"] == str(runner.last_artifacts["md"])

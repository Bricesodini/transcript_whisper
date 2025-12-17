from __future__ import annotations

import os
from pathlib import Path

import pytest

import utils


def test_prepare_paths_rejects_repo_paths(monkeypatch):
    monkeypatch.delenv(utils.LOCAL_DATA_ENV_VAR, raising=False)
    cfg = {"paths": {"work_dir": "work"}}
    repo_root = utils.TS_ROOT
    with pytest.raises(utils.PipelineError):
        utils.prepare_paths(repo_root, cfg)


def test_prepare_paths_allows_override(monkeypatch, tmp_path):
    monkeypatch.setenv(utils.LOCAL_DATA_ENV_VAR, "1")
    cfg = {"paths": {"work_dir": "work"}}
    result = utils.prepare_paths(tmp_path, cfg)
    assert "work_dir" in result
    assert result["work_dir"].exists()


def test_exports_can_be_local_with_flag(monkeypatch, tmp_path):
    monkeypatch.delenv(utils.LOCAL_DATA_ENV_VAR, raising=False)
    cfg = {
        "paths": {
            "exports_dir": "exports",
            "work_dir": str(tmp_path / "work"),
            "logs_dir": str(tmp_path / "logs"),
            "cache_dir": str(tmp_path / "cache"),
        }
    }
    repo_root = utils.TS_ROOT
    result = utils.prepare_paths(repo_root, cfg, allow_local_exports=True)
    assert "exports_dir" in result

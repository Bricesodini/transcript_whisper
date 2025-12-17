from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List

from .settings import Settings


def _check_path(path: Path, expect_dir: bool) -> tuple[bool, str]:
    if not path.exists():
        return False, f"{path} absent"
    if expect_dir and not path.is_dir():
        return False, f"{path} n'est pas un dossier"
    if not expect_dir and not path.is_file():
        return False, f"{path} n'est pas un fichier"
    return True, ""


def run_health_checks(settings: Settings) -> Dict[str, object]:
    checks: List[Dict[str, object]] = []
    issues: List[str] = []

    def add_check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok and detail:
            issues.append(detail)

    ok, detail = _check_path(settings.data_pipeline_root, expect_dir=True)
    add_check("data_pipeline_root", ok, detail)

    ok, detail = _check_path(settings.run_bat_path, expect_dir=False)
    add_check("run_bat_path", ok, detail)

    add_check("jobs_db_write", *_probe_jobs_db(settings))

    status = "ok" if all(check["ok"] for check in checks) else "degraded"
    return {"status": status, "checks": checks, "issues": issues}


def _probe_jobs_db(settings: Settings) -> tuple[bool, str]:
    try:
        conn = sqlite3.connect(settings.jobs_db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS __health_probe__ (id INTEGER PRIMARY KEY)"
        )
        conn.execute("DELETE FROM __health_probe__")
        conn.execute("INSERT INTO __health_probe__ (id) VALUES (1)")
        conn.execute("DELETE FROM __health_probe__ WHERE id = 1")
        conn.commit()
        conn.close()
        return True, ""
    except Exception as exc:  # pragma: no cover - surfaced in health endpoint tests
        return False, str(exc)

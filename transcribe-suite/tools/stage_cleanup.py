from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
from pathlib import Path
from typing import List

IGNORE_PATTERNS = (".git", ".venv", "logs", "__pycache__", "tmp_whisperx", "control_room/frontend/node_modules")
DEFAULT_ARGS = ["--apply", "--no-dry-run", "--write-docs"]


def build_stage_path(repo_root: Path, timestamp: str) -> Path:
    return repo_root.parent / f"{repo_root.name}__cleanup_stage_{timestamp}"


def copy_repo(src: Path, dst: Path) -> None:
    if dst.exists():
        raise RuntimeError(f"Le dossier de staging existe déjà: {dst}")
    ignore = shutil.ignore_patterns(*IGNORE_PATTERNS)
    shutil.copytree(src, dst, ignore=ignore)


def run_cleanup(stage_dir: Path, extra_args: List[str]) -> None:
    if os.name == "nt":
        script = stage_dir / "bin" / "cleanup_repo.bat"
    else:
        script = stage_dir / "bin" / "cleanup_repo.sh"
    if not script.exists():
        raise RuntimeError(f"Script cleanup introuvable: {script}")
    cmd = [str(script), *extra_args]
    result = subprocess.run(cmd, cwd=stage_dir, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"cleanup_repo a échoué (code {result.returncode}).")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Préparer un staging cleanup (copie + apply).")
    parser.add_argument("--repo-root", default=None, help="Racine du dépôt Transcribe Suite.")
    parser.add_argument("--timestamp", default=None, help="Horodatage custom (tests).")
    parser.add_argument(
        "--cleanup-args",
        nargs="*",
        default=DEFAULT_ARGS,
        help="Arguments additionnels transmis à cleanup_repo.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    timestamp = args.timestamp or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    stage_dir = build_stage_path(repo_root, timestamp)
    copy_repo(repo_root, stage_dir)
    run_cleanup(stage_dir, args.cleanup_args)
    print(f"[stage] Cleanup appliqué sur {stage_dir}")
    print("[stage] Inspectez le dossier avant toute action sur le dépôt original.")


if __name__ == "__main__":
    main()

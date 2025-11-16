#!/usr/bin/env python3
"""Orchestre le post-traitement/QA des exports de transcription."""

import argparse
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.append(str(SRC_DIR))

from postproc import PostProcessRunner

DEFAULT_CONFIG = ROOT_DIR / "configs" / "postprocess.default.yaml"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Post-process (normalisation/QA) d'un bundle de transcription.")
    parser.add_argument("--export-dir", required=True, type=Path, help="Répertoire contenant les exports clean.txt/metrics.json...")
    parser.add_argument("--doc-id", type=str, help="Nom du document (sans extension).")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"Config YAML post-process (défaut: {DEFAULT_CONFIG})",
    )
    parser.add_argument("--profile", type=str, help="Profil optionnel à utiliser depuis la config.")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper()))
    runner = PostProcessRunner(args.config, profile=args.profile)
    outputs = runner.run(args.export_dir, doc_id=args.doc_id)
    for key, path in outputs.items():
        print(f"{key}: {path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"[postprocess_transcript] Erreur: {exc}")
        raise

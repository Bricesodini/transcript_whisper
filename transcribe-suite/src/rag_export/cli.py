"""Command-line interface for the RAG export pipeline."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from utils import PipelineError

from . import CONFIG_DIR, DEFAULT_CONFIG_FILENAME
from .configuration import RAGConfigLoader, find_doc_override
from .doctor import RAGDoctor, RAGDoctorOptions
from .query import RAGQuery, RAGQueryOptions
from .runner import RAGExportOptions, RAGExportRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag-export",
        description="Génère et valide des artefacts RAG depuis les sorties existantes.",
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=["export", "doctor", "query"],
        default="export",
        help="Action à exécuter (export par défaut).",
    )
    parser.add_argument("--input", required=True, help="Chemin vers work/<doc>, TRANSCRIPT - <doc> ou dossier RAG.")
    parser.add_argument(
        "--config",
        default=str((CONFIG_DIR / DEFAULT_CONFIG_FILENAME).resolve()),
        help="Fichier de configuration RAG (défaut: config/rag.yaml).",
    )
    parser.add_argument("--doc-config", help="Override spécifique au document (rag.config.yaml).")
    parser.add_argument("--base-url", help="URL de base pour les citations (override CLI).")
    parser.add_argument("--lang", help="Langue forcée (sinon auto/config).")
    parser.add_argument("--force", action="store_true", help="Autorise l'écrasement du dossier cible RAG-<doc>.")
    parser.add_argument("--version-tag", help="Sous-dossier explicite pour RAG-<doc>/<tag>/.")
    parser.add_argument("--no-sqlite", action="store_true", help="Désactive la création de lexical.sqlite.")
    parser.add_argument("--dry-run", action="store_true", help="Simule la génération sans écrire de fichiers.")
    parser.add_argument("--log-level", choices=["debug", "info", "warning", "error"], default="info", help="Niveau de log.")
    parser.add_argument("--doc-id", help="Doc_id manuel (sinon calculé).")
    parser.add_argument(
        "--real-timestamps",
        action="store_true",
        help="Utilise l'heure UTC courante (sinon timestamps déterministes).",
    )
    parser.add_argument("--query", dest="query_text", help="Requête lexicale (action query).")
    parser.add_argument("--top-k", type=int, default=5, help="Nombre de résultats max pour rag query (défaut=5).")
    return parser


def _collect_cli_overrides(args: argparse.Namespace) -> dict:
    overrides = {}
    if args.no_sqlite:
        overrides.setdefault("index", {})["enable_sqlite"] = False
    return overrides


def _resolve_doc_override(args: argparse.Namespace, input_path: Path) -> Optional[Path]:
    if args.doc_config:
        doc_cfg = Path(args.doc_config).expanduser().resolve()
        if not doc_cfg.exists():
            raise PipelineError(f"Config doc introuvable: {doc_cfg}")
        return doc_cfg
    return find_doc_override(input_path)


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        input_path = Path(args.input).expanduser().resolve()
        if not input_path.exists():
            raise PipelineError(f"Chemin introuvable: {input_path}")
        doc_override = _resolve_doc_override(args, input_path)
        cli_overrides = _collect_cli_overrides(args)
        config_loader = RAGConfigLoader(config_path=Path(args.config))
        config_bundle = config_loader.load(doc_override=doc_override, cli_overrides=cli_overrides)

        if args.action == "doctor":
            doctor_options = RAGDoctorOptions(
                input_path=input_path,
                version_tag=args.version_tag,
                doc_id_override=args.doc_id,
            )
            doctor = RAGDoctor(doctor_options, config_bundle, log_level=args.log_level)
            ok = doctor.run()
            return 0 if ok else 3
        if args.action == "query":
            if not args.query_text:
                raise PipelineError("--query est obligatoire pour l'action query.")
            query_options = RAGQueryOptions(
                input_path=input_path,
                query=args.query_text,
                top_k=max(1, int(args.top_k or 5)),
                version_tag=args.version_tag,
                doc_id_override=args.doc_id,
            )
            query_runner = RAGQuery(query_options, config_bundle, log_level=args.log_level)
            query_runner.run()
            return 0

        options = RAGExportOptions(
            input_path=input_path,
            base_url=args.base_url,
            lang=args.lang,
            force=bool(args.force),
            version_tag=args.version_tag,
            no_sqlite=bool(args.no_sqlite),
            dry_run=bool(args.dry_run),
            doc_id_override=args.doc_id,
            real_timestamps=bool(args.real_timestamps),
        )
        runner = RAGExportRunner(options, config_bundle, log_level=args.log_level)
        runner.run()
        return 0
    except PipelineError as exc:
        print(f"[RAG] ERREUR: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"[RAG] ERREUR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

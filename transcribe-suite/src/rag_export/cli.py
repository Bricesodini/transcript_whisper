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
from .lexicon_scan import (
    LexiconApplyCommand,
    LexiconApplyOptions,
    LexiconScanOptions,
    LexiconScanner,
)
from .runner import RAGExportOptions, RAGExportRunner


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rag-export",
        description="Génère et valide des artefacts RAG depuis les sorties existantes.",
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=["export", "doctor", "query", "lexicon"],
        default="export",
        help="Action à exécuter (export par défaut).",
    )
    parser.add_argument(
        "lexicon_action",
        nargs="?",
        choices=["scan", "apply"],
        help="Action lexicon (scan/apply).",
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
    parser.add_argument("--top-k", type=int, help="Nombre de résultats max pour rag query (défaut=5).")
    parser.add_argument("--min-count", dest="lexicon_min_count", type=int, help="Seuil de fréquence pour lexicon scan.")
    parser.add_argument("--out", dest="lexicon_out", help="Fichier de sortie pour les suggestions lexicon.")
    parser.add_argument(
        "--from",
        dest="lexicon_from_path",
        help="Fichier source rag.glossary.suggested.yaml pour lexicon apply.",
    )
    parser.add_argument(
        "--to",
        dest="lexicon_to_path",
        help="Fichier cible rag.glossary.yaml pour lexicon apply.",
    )
    parser.add_argument(
        "--keep-top",
        dest="lexicon_keep_top",
        type=int,
        help="Nombre maximum de règles à conserver lors du lexicon apply.",
    )
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
            top_k = args.top_k if args.top_k is not None else 5
            query_options = RAGQueryOptions(
                input_path=input_path,
                query=args.query_text,
                top_k=max(1, int(top_k)),
                version_tag=args.version_tag,
                doc_id_override=args.doc_id,
            )
            query_runner = RAGQuery(query_options, config_bundle, log_level=args.log_level)
            query_runner.run()
            return 0
        if args.action == "lexicon":
            if not args.lexicon_action:
                raise PipelineError("Préciser l'action lexicon (scan ou apply).")
            if args.lexicon_action == "scan":
                lex_top_k = args.top_k if args.top_k is not None else 200
                scan_options = LexiconScanOptions(
                    input_path=input_path,
                    min_count=max(1, int(args.lexicon_min_count or 2)),
                    top_k=max(1, int(lex_top_k)),
                    output_path=Path(args.lexicon_out).expanduser().resolve() if args.lexicon_out else None,
                    doc_id_override=args.doc_id,
                    version_tag=args.version_tag,
                )
                scanner = LexiconScanner(scan_options, config_bundle, log_level=args.log_level)
                scanner.run()
                return 0
            apply_options = LexiconApplyOptions(
                input_path=input_path,
                source_path=Path(args.lexicon_from_path).expanduser().resolve() if args.lexicon_from_path else None,
                target_path=Path(args.lexicon_to_path).expanduser().resolve() if args.lexicon_to_path else None,
                keep_top=args.lexicon_keep_top,
                doc_id_override=args.doc_id,
                version_tag=args.version_tag,
            )
            applier = LexiconApplyCommand(apply_options, config_bundle, log_level=args.log_level)
            applier.run()
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

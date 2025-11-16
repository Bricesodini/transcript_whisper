#!/usr/bin/env python3
"""Validation helpers for polished transcript exports."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence


def _resolve_export_paths(export_dir: Path, base_name: Optional[str]) -> tuple[str, Dict[str, Path]]:
    if base_name:
        clean_jsonl = export_dir / f"{base_name}.clean.jsonl"
        if not clean_jsonl.exists():
            raise FileNotFoundError(f"Fichier introuvable: {clean_jsonl}")
    else:
        try:
            clean_jsonl = next(export_dir.glob("*.clean.jsonl"))
        except StopIteration as exc:  # pragma: no cover - defensive
            raise FileNotFoundError(f"Aucun *.clean.jsonl trouvé dans {export_dir}") from exc
        base_name = clean_jsonl.name[: -len(".clean.jsonl")]
    paths = {
        "clean_jsonl": clean_jsonl,
        "chunks": export_dir / f"{base_name}.chunks.jsonl",
        "low_conf": export_dir / f"{base_name}.low_confidence.jsonl",
        "metrics": export_dir / f"{base_name}.metrics.json",
    }
    return base_name, paths


def _load_jsonl(path: Path) -> List[Dict]:
    rows: List[Dict] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if raw:
                rows.append(json.loads(raw))
    return rows


def _collect_confidence_issues(clean_entries: Sequence[Dict], chunk_entries: Sequence[Dict]) -> List[str]:
    issues: List[str] = []
    for entry in clean_entries:
        tokens = entry.get("meta", {}).get("tokens", 0)
        if tokens and entry.get("confidence_mean") is None:
            issues.append(f"Phrase sans confidence_mean: {entry.get('id')}")
    for chunk in chunk_entries:
        if chunk.get("sentence_count") and chunk.get("confidence_mean") is None:
            issues.append(f"Chunk sans confidence_mean: {chunk.get('id')}")
    return issues


def validate_export_bundle(export_dir: Path, base_name: Optional[str] = None) -> None:
    export_dir = Path(export_dir)
    base_name, paths = _resolve_export_paths(export_dir, base_name)
    clean_entries = _load_jsonl(paths["clean_jsonl"])
    chunk_entries = _load_jsonl(paths["chunks"])
    low_conf_entries = _load_jsonl(paths["low_conf"])
    metrics = json.loads(paths["metrics"].read_text(encoding="utf-8"))

    issues: List[str] = []
    if metrics.get("phrases_total") != len(clean_entries):
        issues.append("phrases_total ne correspond pas au nombre de lignes dans clean.jsonl")
    if metrics.get("chunks_total") != len(chunk_entries):
        issues.append("chunks_total ne correspond pas au nombre de lignes dans chunks.jsonl")
    if metrics.get("low_conf_count") != len(low_conf_entries):
        issues.append("low_conf_count ne correspond pas au nombre de lignes dans low_confidence.jsonl")

    artifacts = (metrics.get("artifacts") or {}).get("low_confidence") or {}
    artifact_path = export_dir / artifacts.get("path", f"{base_name}.low_confidence.jsonl")
    expected_bytes = artifacts.get("bytes")
    expected_count = artifacts.get("count")
    if not artifact_path.exists():
        issues.append(f"Artifact low_confidence manquant: {artifact_path}")
    else:
        size = artifact_path.stat().st_size
        if expected_bytes is not None and expected_bytes != size:
            issues.append("Taille low_confidence.jsonl incohérente dans metrics.artifacts")
        if expected_count is not None and expected_count != len(low_conf_entries):
            issues.append("Compteur low_confidence.artifacts.count incohérent")

    issues.extend(_collect_confidence_issues(clean_entries, chunk_entries))
    if issues:
        formatted = "\n- ".join(issues)
        raise RuntimeError(f"Validation des exports échouée:\n- {formatted}")


def cli_main() -> None:
    parser = argparse.ArgumentParser(description="Valide les exports d'un transcript (clean/chunks/low_confidence/metrics).")
    parser.add_argument("--export-dir", type=Path, required=True, help="Répertoire contenant les exports (clean.jsonl, metrics.json, etc.)")
    parser.add_argument("--doc-id", type=str, help="Nom de base du document (sans extension)")
    args = parser.parse_args()
    validate_export_bundle(args.export_dir, args.doc_id)


if __name__ == "__main__":
    try:
        cli_main()
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"[validate_outputs] Erreur: {exc}")
        raise

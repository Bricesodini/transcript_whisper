from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_DIRS = [
    ("01_input", "Inputs bruts"),
    ("02_output_source", "Sources ASR + work"),
    ("03_output_RAG", "Exports RAG"),
    ("04_archive", "Archives"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit / archive du NAS Transcribe Suite.")
    parser.add_argument("--root", default=os.getenv("DATA_PIPELINE_ROOT"), help="Racine du NAS (DATA_PIPELINE_ROOT).")
    parser.add_argument("--report", default="docs/NAS_AUDIT.md", help="Rapport Markdown.")
    parser.add_argument("--json", dest="json_report", default="docs/NAS_AUDIT.json", help="Rapport JSON.")
    parser.add_argument("--out-dir", default="logs", help="Dossier de sortie (par défaut logs/).")
    parser.add_argument("--write-docs", action="store_true", help="Écrit les rapports dans docs/ (sinon logs timestampé).")
    parser.add_argument("--archive", action="append", dest="archive_docs", default=[], help="Doc à archiver (répétable).")
    parser.add_argument("--apply", action="store_true", help="Applique les déplacements vers 04_archive.")
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="Ne déplace rien (défaut). Utilisez --no-dry-run pour valider.",
    )
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Autorise les déplacements.")
    return parser.parse_args()


def gather_dir_stats(path: Path) -> Dict[str, object]:
    exists = path.exists()
    info = {
        "path": str(path),
        "exists": exists,
        "size_bytes": 0,
        "items": 0,
        "oldest": None,
        "newest": None,
    }
    if not exists:
        return info
    oldest: Optional[float] = None
    newest: Optional[float] = None
    total_size = 0
    total_items = 0
    for file_path in _iter_files(path):
        try:
            stat = file_path.stat()
        except OSError:
            continue
        total_items += 1
        total_size += stat.st_size
        ts = stat.st_mtime
        if oldest is None or ts < oldest:
            oldest = ts
        if newest is None or ts > newest:
            newest = ts
    info["size_bytes"] = total_size
    info["items"] = total_items
    info["oldest"] = _format_ts(oldest)
    info["newest"] = _format_ts(newest)
    return info


def _iter_files(path: Path) -> Iterable[Path]:
    if not path.exists():
        return []
    for child in path.rglob("*"):
        if child.is_file():
            yield child


def _format_ts(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat(timespec="seconds")


def list_asr_docs(root: Path) -> Dict[str, int]:
    staging = root / "02_output_source" / "asr"
    docs: Dict[str, int] = {}
    if not staging.exists():
        return docs
    for doc_dir in staging.iterdir():
        if not doc_dir.is_dir():
            continue
        docs[doc_dir.name] = _dir_size(doc_dir)
    return docs


def list_rag_docs(root: Path) -> Dict[str, int]:
    rag_root = root / "03_output_RAG"
    docs: Dict[str, int] = {}
    if not rag_root.exists():
        return docs
    for doc_dir in rag_root.iterdir():
        if not doc_dir.is_dir() or not doc_dir.name.startswith("RAG-"):
            continue
        doc_id = doc_dir.name[4:]
        docs[doc_id] = _dir_size(doc_dir)
    return docs


def _dir_size(path: Path) -> int:
    total = 0
    for file_path in _iter_files(path):
        try:
            total += file_path.stat().st_size
        except OSError:
            continue
    return total


def build_audit(root: Path, top: int = 10) -> Dict[str, object]:
    dirs_info = []
    for folder, label in DEFAULT_DIRS:
        dirs_info.append({"label": label, **gather_dir_stats(root / folder)})
    asr_docs = list_asr_docs(root)
    rag_docs = list_rag_docs(root)
    heavy_docs = sorted(asr_docs.items(), key=lambda item: item[1], reverse=True)[:top]
    orphans = {
        "missing_rag": sorted([doc for doc in asr_docs.keys() if doc not in rag_docs]),
        "missing_source": sorted([doc for doc in rag_docs.keys() if doc not in asr_docs]),
    }
    return {
        "root": str(root),
        "generated_at": _now().isoformat(timespec="seconds"),
        "directories": dirs_info,
        "heavy_docs": [{"doc": doc, "size_bytes": size} for doc, size in heavy_docs],
        "orphans": orphans,
    }


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def resolve_output_paths(
    root: Path,
    *,
    write_docs: bool,
    report: str,
    json_path: str,
    out_dir: str,
    timestamp: Optional[str] = None,
) -> Tuple[Path, Path, str]:
    stamp = timestamp or _now().strftime("%Y%m%d_%H%M%S")
    if write_docs:
        md_path = (root / report).resolve()
        json_out = (root / json_path).resolve()
    else:
        dst = (root / out_dir).resolve()
        dst.mkdir(parents=True, exist_ok=True)
        md_path = dst / f"nas_audit_{stamp}.md"
        json_out = dst / f"nas_audit_{stamp}.json"
    return md_path, json_out, stamp


def write_report(report_path: Path, payload: Dict[str, object]) -> None:
    lines = [
        "# NAS Audit Report",
        "",
        f"Généré le {payload['generated_at']}",
        "",
        f"- Racine : `{payload['root']}`",
        "",
        "## Répertoires principaux",
        "",
        "| Label | Path | Taille | Fichiers | Oldest | Newest |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry in payload["directories"]:
        size_gb = entry["size_bytes"] / (1024**3)
        lines.append(
            f"| {entry['label']} | `{entry['path']}` | {size_gb:.2f} GB | {entry['items']} | "
            f"{entry['oldest'] or '-'} | {entry['newest'] or '-'} |"
        )
    lines.append("")
    lines.append("## Docs les plus lourds (ASR staging)")
    lines.append("")
    if not payload["heavy_docs"]:
        lines.append("_Aucun document détecté._")
    else:
        lines.append("| Doc | Taille (GB) |")
        lines.append("| --- | --- |")
        for doc in payload["heavy_docs"]:
            lines.append(f"| {doc['doc']} | {doc['size_bytes'] / (1024**3):.2f} |")
    lines.append("")
    lines.append("## Orphelins")
    lines.append("")
    lines.append(f"- RAG sans source : {', '.join(payload['orphans']['missing_source']) or 'Aucun'}")
    lines.append(f"- Source sans RAG : {', '.join(payload['orphans']['missing_rag']) or 'Aucun'}")
    lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def archive_docs(root: Path, docs: List[str], *, dry_run: bool) -> List[Tuple[Path, Path]]:
    moves: List[Tuple[Path, Path]] = []
    if not docs:
        return moves
    archive_root = root / "04_archive"
    asr_source = root / "02_output_source" / "asr"
    rag_root = root / "03_output_RAG"
    for doc in docs:
        src_asr = asr_source / doc
        if src_asr.exists():
            dst_asr = archive_root / "02_output_source" / "asr" / doc
            moves.append((src_asr, dst_asr))
            if not dry_run:
                dst_asr.parent.mkdir(parents=True, exist_ok=True)
                src_asr.rename(dst_asr)
        src_rag = rag_root / f"RAG-{doc}"
        if src_rag.exists():
            dst_rag = archive_root / "03_output_RAG" / f"RAG-{doc}"
            moves.append((src_rag, dst_rag))
            if not dry_run:
                dst_rag.parent.mkdir(parents=True, exist_ok=True)
                src_rag.rename(dst_rag)
    return moves


def main() -> None:
    args = parse_args()
    if not args.root:
        raise SystemExit("DATA_PIPELINE_ROOT non défini (utiliser --root).")
    root = Path(args.root).expanduser().resolve()
    payload = build_audit(root)
    report_path, json_path, _ = resolve_output_paths(
        root,
        write_docs=bool(args.write_docs),
        report=args.report,
        json_path=args.json_report,
        out_dir=args.out_dir,
    )
    write_report(report_path, payload)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[nas] Rapport écrit dans {report_path}")
    print(f"[nas] JSON écrit dans {json_path}")
    if args.archive_docs:
        moves = archive_docs(root, args.archive_docs, dry_run=args.dry_run or not args.apply)
        for src, dst in moves:
            verb = "Prévu" if args.dry_run or not args.apply else "Déplacé"
            print(f"[nas] {verb}: {src} -> {dst}")
        if args.archive_docs and not moves:
            print("[nas] Aucun document à archiver.")


if __name__ == "__main__":
    main()

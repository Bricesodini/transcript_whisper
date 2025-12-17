from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

KEYWORDS: Sequence[str] = (
    "exports_dir",
    "inputs_dir",
    "outputs_dir",
    "work_dir",
    "inputs",
    "outputs",
    "exports",
    "work",
    "data",
    "tmp",
    "runs",
)
LEGACY_DIRS: Sequence[str] = ("inputs", "outputs", "exports", "work", "data", "tmp", "runs")
FILE_EXTENSIONS = {".py", ".yaml", ".yml", ".md", ".bat", ".ps1", ".sh", ".json", ".cfg"}
KNOWN_OK_DIRS = {
    "bin",
    "src",
    "tests",
    "config",
    "configs",
    "control_room",
    "docs",
    "tools",
    "cache",
    "logs",
    "share_stage",
    "work_in",
    "tmp_whisperx",
}


@dataclass
class Reference:
    pattern: str
    file: Path
    line_no: int
    context: str
    scope: str
    action: str


@dataclass
class DirectoryFinding:
    path: Path
    exists: bool
    action: str
    notes: str


@dataclass
class InventoryEntry:
    path: Path
    kind: str
    size_bytes: int
    modified_at: Optional[str]
    classification: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "path": str(self.path),
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "classification": self.classification,
        }


def collect_references(files: Iterable[Path], keywords: Sequence[str]) -> List[Reference]:
    refs: List[Reference] = []
    ordered_keywords = sorted(set(keywords), key=len, reverse=True)
    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        lower_lines = text.splitlines()
        for lineno, line in enumerate(lower_lines, 1):
            low_line = line.lower()
            for keyword in ordered_keywords:
                if keyword in low_line:
                    scope = classify_scope(file_path)
                    action = "Garder (tests)" if scope == "tests" else "Déprécier/rediriger"
                    refs.append(
                        Reference(
                            pattern=keyword,
                            file=file_path,
                            line_no=lineno,
                            context=line.strip(),
                            scope=scope,
                            action=action,
                        )
                    )
                    break
    return refs


def classify_scope(path: Path) -> str:
    parts = [part.lower() for part in path.parts]
    if "tests" in parts or "fixtures" in parts:
        return "tests"
    if "docs" in parts:
        return "docs"
    if "bin" in parts:
        return "scripts"
    return "source"


def enumerate_files(root: Path) -> List[Path]:
    candidates: List[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in FILE_EXTENSIONS:
            continue
        candidates.append(path)
    return candidates


def audit_directories(root: Path) -> List[DirectoryFinding]:
    findings: List[DirectoryFinding] = []
    for name in LEGACY_DIRS:
        candidate = root / name
        exists = candidate.exists()
        note = "dossier historique" if exists else "absent"
        action = "Renommer/déplacer hors dépôt" if exists else "RAS"
        findings.append(DirectoryFinding(path=candidate, exists=exists, action=action, notes=note))
    return findings


def build_report(
    root: Path,
    dir_findings: List[DirectoryFinding],
    references: List[Reference],
) -> str:
    lines: List[str] = []
    lines.append("# Cleanup Audit Report")
    lines.append("")
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    lines.append(f"Généré automatiquement le {timestamp}")
    lines.append("")
    lines.append("## Racine auditée")
    lines.append("")
    lines.append(f"- `{root}`")
    lines.append("")
    lines.append("## Dossiers historiques")
    lines.append("")
    lines.append("| Dossier | Existe | Action | Notes |")
    lines.append("| --- | --- | --- | --- |")
    for finding in dir_findings:
        lines.append(
            f"| `{finding.path}` | {'✅' if finding.exists else '❌'} | {finding.action} | {finding.notes} |"
        )
    lines.append("")
    lines.append("## Références code / scripts")
    lines.append("")
    if not references:
        lines.append("_Aucune référence détectée._")
    else:
        lines.append("| Pattern | Fichier | Ligne | Contexte | Scope | Action suggérée |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for ref in references:
            rel = ref.file
            lines.append(
                f"| `{ref.pattern}` | `{rel}` | {ref.line_no} | `{ref.context}` | {ref.scope} | {ref.action} |"
            )
    lines.append("")
    return "\n".join(lines)


def build_inventory(root: Path) -> List[InventoryEntry]:
    entries: List[InventoryEntry] = []
    targets = {name for name in LEGACY_DIRS}
    for child in root.iterdir():
        if child.name.startswith("_deprecated_"):
            targets.add(child.name)
    for name in sorted(targets):
        candidate = root / name
        classification = classify_entry(name, candidate.exists())
        entries.append(
            InventoryEntry(
                path=candidate,
                kind="directory",
                size_bytes=_dir_size(candidate) if candidate.exists() else 0,
                modified_at=_format_mtime(candidate),
                classification=classification,
            )
        )
    return entries


def classify_entry(name: str, exists: bool) -> str:
    lowered = name.lower()
    if not exists:
        return "missing"
    if lowered.startswith("_deprecated"):
        return "deprecated"
    if lowered in LEGACY_DIRS:
        return "legacy"
    if lowered in KNOWN_OK_DIRS:
        return "ok"
    return "unknown"


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if not child.is_file():
            continue
        try:
            total += child.stat().st_size
        except OSError:
            continue
    return total


def _format_mtime(path: Path) -> Optional[str]:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat(timespec="seconds")


def next_deprecated_target(root: Path, name: str) -> Path:
    base = f"_deprecated_{name}"
    candidate = root / base
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base}_{suffix}"
        suffix += 1
    return candidate


def determine_actions(root: Path, dir_findings: List[DirectoryFinding]) -> List[Dict[str, str]]:
    actions: List[Dict[str, str]] = []
    for finding in dir_findings:
        lowered = finding.path.name.lower()
        if not finding.exists or lowered not in LEGACY_DIRS:
            continue
        target = next_deprecated_target(root, finding.path.name)
        actions.append(
            {
                "type": "rename_legacy_dir",
                "source": str(finding.path),
                "target": str(target),
                "note": finding.notes,
            }
        )
    return actions


def rename_legacy_dirs(actions: List[Dict[str, str]], *, dry_run: bool, logger) -> List[Tuple[Path, Path]]:
    changes: List[Tuple[Path, Path]] = []
    for action in actions:
        source = Path(action["source"])
        target = Path(action["target"])
        if not source.exists():
            continue
        changes.append((source, target))
        if dry_run:
            logger(f"[audit] Prévu: {source} -> {target}")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source.rename(target)
        readme = target / "README.md"
        readme.write_text(
            "# Dossier déprécié\n\n"
            "Ce dossier était utilisé dans les anciennes versions pour stocker des données locales.\n"
            "Merci de ne plus y écrire : utilisez `DATA_PIPELINE_ROOT` ou le partage NAS.\n",
            encoding="utf-8",
        )
        logger(f"[audit] Renommé: {source} -> {target}")
    return changes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit des chemins locaux Transcribe Suite.")
    parser.add_argument("--repo-root", default=None, help="Racine du dépôt (défaut: auto).")
    parser.add_argument("--write-report", default="docs/CLEANUP_AUDIT.md", help="Chemin du rapport Markdown.")
    parser.add_argument("--write-plan", default="docs/CLEANUP_PLAN.json", help="Chemin du plan JSON.")
    parser.add_argument("--out-dir", default="logs", help="Dossier de sortie (defaut: logs/).")
    parser.add_argument("--write-docs", action="store_true", help="Écrit les rapports dans docs/ (sinon logs/<timestamp>).")
    parser.add_argument("--log-file", default=None, help="Fichier log texte (optionnel).")
    parser.add_argument("--apply", action="store_true", help="Applique le plan (renommage vers _deprecated_*).")
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="N'applique aucun changement (défaut).",
    )
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Autorise les changements lors de --apply.")
    parser.add_argument(
        "--fail-on-legacy",
        action="store_true",
        help="Code retour !=0 si des dossiers legacy existent encore.",
    )
    return parser.parse_args()


def gather_files(project_root: Path) -> List[Path]:
    files: List[Path] = []
    seen: set[Path] = set()
    candidates = [project_root, project_root.parent / "bin"]
    for base in candidates:
        if not base.exists():
            continue
        for file_path in enumerate_files(base):
            if file_path in seen:
                continue
            seen.add(file_path)
            files.append(file_path)
    top_readme = project_root.parent / "README.md"
    if top_readme.exists() and top_readme not in seen:
        files.append(top_readme)
    return files


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def resolve_output_paths(
    repo_root: Path,
    *,
    write_docs: bool,
    write_report: str,
    write_plan: str,
    out_dir: str,
    timestamp: Optional[str] = None,
) -> Tuple[Path, Path, str]:
    stamp = timestamp or _now().strftime("%Y%m%d_%H%M%S")
    base_root = repo_root.parent
    if write_docs:
        report_path = (base_root / write_report).resolve()
        plan_path = (base_root / write_plan).resolve()
    else:
        output_dir = (base_root / out_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"cleanup_audit_{stamp}.md"
        plan_path = output_dir / f"cleanup_plan_{stamp}.json"
    return report_path, plan_path, stamp


def main() -> None:
    args = parse_args()
    project_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    files = gather_files(project_root)
    references = collect_references(files, KEYWORDS)
    dir_findings = audit_directories(project_root)
    inventory = build_inventory(project_root)
    actions = determine_actions(project_root, dir_findings)
    report = build_report(project_root, dir_findings, references)
    report_path, plan_path, _ = resolve_output_paths(
        project_root,
        write_docs=bool(args.write_docs),
        write_report=args.write_report,
        write_plan=args.write_plan,
        out_dir=args.out_dir,
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    plan_payload = {
        "generated_at": _now().isoformat(timespec="seconds"),
        "repo_root": str(project_root),
        "dry_run": bool(args.dry_run),
        "inventory": [entry.to_dict() for entry in inventory],
        "references": [
            {
                "pattern": ref.pattern,
                "file": str(ref.file),
                "line": ref.line_no,
                "context": ref.context,
                "scope": ref.scope,
            }
            for ref in references
        ],
        "actions": actions,
    }
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(plan_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def log(message: str) -> None:
        print(message)
        if args.log_file:
            log_file = Path(args.log_file)
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n")

    log(f"[audit] Rapport écrit dans {report_path}")
    log(f"[audit] Plan écrit dans {plan_path}")

    if args.apply:
        if args.dry_run:
            log("[audit] --apply demandé mais --dry-run actif (aucun changement).")
        else:
            rename_legacy_dirs(actions, dry_run=False, logger=log)
    else:
        rename_legacy_dirs(actions, dry_run=True, logger=log)

    if args.fail_on_legacy and actions:
        log("[audit] Dossiers legacy détectés.")
        sys.exit(2)


if __name__ == "__main__":
    main()

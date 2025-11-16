from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .models import AssetBundle


def locate_assets(export_dir: Path, base_name: Optional[str] = None) -> AssetBundle:
    export_dir = Path(export_dir).expanduser().resolve()
    if not export_dir.exists():
        raise FileNotFoundError(f"Répertoire export introuvable: {export_dir}")
    if base_name:
        base = base_name
        clean_txt = export_dir / f"{base}.clean.txt"
        if not clean_txt.exists():
            raise FileNotFoundError(f"clean.txt manquant pour {base}")
    else:
        try:
            clean_txt = next(export_dir.glob("*.clean.txt"))
        except StopIteration as exc:
            raise FileNotFoundError(f"Aucun fichier *.clean.txt dans {export_dir}") from exc
        base = clean_txt.name[: -len(".clean.txt")]
    metrics = export_dir / f"{base}.metrics.json"
    low_conf = export_dir / f"{base}.low_confidence.jsonl"
    chapters = export_dir / f"{base}.chapters.json"
    quotes = export_dir / f"{base}.quotes.jsonl"
    return AssetBundle(
        export_dir=export_dir,
        base_name=base,
        clean_txt=clean_txt,
        metrics_json=metrics,
        low_conf_jsonl=low_conf if low_conf.exists() else None,
        chapters_json=chapters if chapters.exists() else None,
        quotes_jsonl=quotes if quotes.exists() else None,
    )


def read_metrics(bundle: AssetBundle) -> Dict:
    if not bundle.metrics_json.exists():
        raise FileNotFoundError(f"metrics.json manquant: {bundle.metrics_json}")
    return json.loads(bundle.metrics_json.read_text(encoding="utf-8"))


def read_clean_lines(bundle: AssetBundle) -> List[str]:
    content = bundle.clean_txt.read_text(encoding="utf-8")
    lines = content.splitlines()
    return lines


def read_jsonl(path: Optional[Path]) -> List[Dict]:
    if not path or not path.exists():
        return []
    rows: List[Dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if raw:
                rows.append(json.loads(raw))
    return rows


def diagnose_bundle(bundle: AssetBundle, lines: Sequence[str], metrics: Dict) -> Dict:
    issues: List[str] = []
    phrases_total = len(lines)
    metric_phrases = metrics.get("phrases_total")
    if metric_phrases and metric_phrases != phrases_total:
        issues.append(f"metrics.phrases_total={metric_phrases} != lignes_clean={phrases_total}")
    low_conf_count = int(metrics.get("low_conf_count") or 0)
    low_conf_path = bundle.low_conf_jsonl
    if low_conf_count > 0 and (not low_conf_path or low_conf_path.stat().st_size == 0):
        issues.append("low_conf_count > 0 mais low_confidence.jsonl est absent ou vide")
    return {
        "phrases_total": phrases_total,
        "low_conf_count": low_conf_count,
        "issues": issues,
    }


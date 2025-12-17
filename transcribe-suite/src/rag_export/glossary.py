"""Helpers for loading and merging glossary rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

from utils import PipelineError


GlossaryRule = Dict[str, Any]


def load_glossary_rules(path: Path) -> List[GlossaryRule]:
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:  # pragma: no cover
        raise PipelineError(f"Glossaire invalide: {path} ({exc})") from exc
    rules = data.get("rules")
    if not isinstance(rules, list):
        return []
    normalized: List[GlossaryRule] = []
    for entry in rules:
        if not isinstance(entry, dict):
            continue
        pattern = entry.get("pattern")
        replacement = entry.get("replacement")
        if not pattern or replacement is None:
            continue
        normalized.append({"pattern": str(pattern), "replacement": str(replacement)})
    return normalized


def merge_glossary_rules(*collections: Iterable[GlossaryRule]) -> List[GlossaryRule]:
    merged: List[GlossaryRule] = []
    seen = set()
    for collection in collections:
        for rule in collection or []:
            pattern = rule.get("pattern")
            replacement = rule.get("replacement")
            if not pattern or replacement is None:
                continue
            key = (pattern, replacement)
            if key in seen:
                continue
            seen.add(key)
            merged.append({"pattern": pattern, "replacement": replacement})
    return merged


def write_glossary_file(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        yaml.safe_dump(payload, fh, allow_unicode=True, sort_keys=False)

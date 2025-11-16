from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_postprocess_config(path: Path, profile: Optional[str] = None) -> Dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config introuvable: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = raw.get("defaults", {})
    profile_name = profile or defaults.get("profile") or "default"
    profiles = raw.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("profiles doit être un dictionnaire")
    base_profile = profiles.get("default", {})
    selected = profiles.get(profile_name, {})
    if profile_name != "default" and not selected:
        raise ValueError(f"Profil introuvable dans la config: {profile_name}")
    profile_cfg = _deep_merge(base_profile, selected)
    return {
        "general": defaults,
        "profile": profile_cfg,
    }


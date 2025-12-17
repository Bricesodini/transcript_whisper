"""Configuration helpers for the RAG export pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from utils import load_config, merge_dict

from . import CONFIG_DIR, DEFAULT_CONFIG_FILENAME, DEFAULT_DOC_CONFIG_FILENAME


def _read_yaml(path: Optional[Path]) -> Dict[str, Any]:
    if not path:
        return {}
    if not path.exists():
        return {}
    return load_config(path)


def _extract_rag_section(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not raw:
        return {}
    if "rag" in raw and isinstance(raw["rag"], dict):
        return raw["rag"]
    return raw


@dataclass
class ConfigBundle:
    """Carries the effective config plus provenance metadata."""

    effective: Dict[str, Any]
    base_path: Path
    doc_override_path: Optional[Path] = None
    cli_overrides: Dict[str, Any] = field(default_factory=dict)
    raw_base: Dict[str, Any] = field(default_factory=dict)
    raw_override: Dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "base_path": str(self.base_path),
            "doc_override_path": str(self.doc_override_path) if self.doc_override_path else None,
            "cli_overrides": self.cli_overrides,
            "effective": self.effective,
        }


class RAGConfigLoader:
    """Loads RAG configuration from base + doc override + CLI overrides."""

    def __init__(self, *, config_path: Optional[Path] = None):
        self.config_path = (config_path or CONFIG_DIR / DEFAULT_CONFIG_FILENAME).resolve()
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration RAG introuvable: {self.config_path}")

    def load(
        self,
        *,
        doc_override: Optional[Path] = None,
        cli_overrides: Optional[Dict[str, Any]] = None,
    ) -> ConfigBundle:
        base_raw = _read_yaml(self.config_path)
        override_raw = _read_yaml(doc_override)
        base_section = _extract_rag_section(base_raw)
        override_section = _extract_rag_section(override_raw)

        merged = merge_dict(base_section, override_section)
        if cli_overrides:
            merged = merge_dict(merged, cli_overrides)

        return ConfigBundle(
            effective=merged,
            base_path=self.config_path,
            doc_override_path=doc_override,
            cli_overrides=cli_overrides or {},
            raw_base=base_raw,
            raw_override=override_raw,
        )


def find_doc_override(input_path: Path) -> Optional[Path]:
    """Return doc-specific override path if present next to the input."""
    candidates = [
        input_path / DEFAULT_DOC_CONFIG_FILENAME,
        input_path.parent / DEFAULT_DOC_CONFIG_FILENAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None

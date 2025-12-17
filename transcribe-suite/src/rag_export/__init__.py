"""RAG export package metadata and helpers."""

from pathlib import Path

RAG_SCHEMA_VERSION = "0.1.0"
DEFAULT_CONFIG_FILENAME = "rag.yaml"
DEFAULT_DOC_CONFIG_FILENAME = "rag.config.yaml"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"

__all__ = [
    "RAG_SCHEMA_VERSION",
    "DEFAULT_CONFIG_FILENAME",
    "DEFAULT_DOC_CONFIG_FILENAME",
    "PROJECT_ROOT",
    "CONFIG_DIR",
]

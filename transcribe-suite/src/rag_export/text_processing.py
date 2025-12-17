"""Text processing helpers for the RAG export pipeline."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, List, Sequence

try:  # pragma: no cover - optional dependency
    from ftfy import fix_text as _ftfy_fix_text
except ImportError:  # pragma: no cover
    _ftfy_fix_text = None

_MOJIBAKE_REGEX = re.compile(
    r"(?:Ã[\x80-\xBF]|Â[\x80-\xBF]|â€™|â€œ|â€\S|â€“|â€”|â€¢|â€¦|â„¢)",
    re.UNICODE,
)
_MOJIBAKE_TOKENS = ("Ã", "Â", "â€™", "â€œ", "â€", "â€“", "â€”", "â€¢", "â€¦", "â„¢")

DEFAULT_ACRONYMS: Sequence[str] = (
    "IA",
    "HTML",
    "CSS",
    "API",
    "GPU",
    "CPU",
    "AI",
    "NLP",
    "LLM",
    "SQL",
    "HTTP",
    "HTTPS",
    "CLI",
    "ChatGPT",
    "Claude",
)


@dataclass
class GlossaryRule:
    pattern: re.Pattern[str]
    replacement: str


def detect_mojibake(text: str) -> bool:
    """Heuristically detect mojibake sequences."""
    if not text:
        return False
    haystack = str(text)
    if any(token in haystack for token in _MOJIBAKE_TOKENS):
        return True
    return bool(_MOJIBAKE_REGEX.search(haystack))


def fix_mojibake(text: str) -> str:
    """Fix mojibake using ftfy when possible, falling back to latin-1 dance."""
    if text is None:
        return ""
    raw = str(text)
    if not raw:
        return ""
    if detect_mojibake(raw):
        if _ftfy_fix_text is not None:
            fixed = _ftfy_fix_text(raw)
        else:
            try:
                fixed = raw.encode("latin-1", errors="ignore").decode("utf-8", errors="replace")
            except UnicodeEncodeError:
                fixed = raw
        return unicodedata.normalize("NFC", fixed)
    return unicodedata.normalize("NFC", raw)


def compile_glossary_rules(entries: Iterable[dict]) -> List[GlossaryRule]:
    rules: List[GlossaryRule] = []
    for entry in entries or []:
        pattern = (entry or {}).get("pattern")
        replacement = (entry or {}).get("replacement") or ""
        if not pattern:
            continue
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
        except re.error:
            continue
        rules.append(GlossaryRule(pattern=compiled, replacement=replacement))
    return rules


def normalize_for_embedding(
    text: str,
    *,
    acronyms: Sequence[str] | None = None,
    glossary_rules: Sequence[GlossaryRule] | None = None,
) -> str:
    """Produce a cleaned, casing-stable variant for embeddings."""
    if not text:
        return ""
    normalized = fix_mojibake(text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\u00A0", " ")
    normalized = _normalize_spaces(normalized)
    normalized = normalized.lower()
    normalized = _ensure_space_after_punctuation(normalized)
    normalized = _sentence_case(normalized)
    normalized = _apply_acronyms(normalized, acronyms or DEFAULT_ACRONYMS)
    normalized = _apply_glossary(normalized, glossary_rules or [])
    normalized = _dedupe_sentences(normalized)
    normalized = unicodedata.normalize("NFC", normalized)
    return normalized.strip()


def _normalize_spaces(text: str) -> str:
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _ensure_space_after_punctuation(text: str) -> str:
    return re.sub(r"([.!?])(?=[^\s])", r"\1 ", text)


def _sentence_case(text: str) -> str:
    result: List[str] = []
    capitalize_next = True
    for ch in text:
        if capitalize_next and ch.isalpha():
            result.append(ch.upper())
            capitalize_next = False
            continue
        result.append(ch)
        if ch in ".!?":
            capitalize_next = True
        elif ch.strip():
            capitalize_next = False
    return "".join(result)


def _apply_acronyms(text: str, acronyms: Sequence[str]) -> str:
    output = text
    for acronym in acronyms:
        if not acronym:
            continue
        pattern = re.compile(rf"\b{re.escape(acronym)}\b", re.IGNORECASE)
        output = pattern.sub(acronym, output)
    return output


def _apply_glossary(text: str, rules: Sequence[GlossaryRule]) -> str:
    output = text
    for rule in rules:
        output = rule.pattern.sub(rule.replacement, output)
    return output


def _dedupe_sentences(text: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    seen = set()
    kept: List[str] = []
    for sentence in sentences:
        candidate = sentence.strip()
        if not candidate:
            continue
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(candidate)
    return " ".join(kept)

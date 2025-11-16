"""Shared helpers for text normalization/polish across exports."""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

PUNCT_DUPLICATES_RE = re.compile(r"([,.!?;:])\1+")
DOT_COMMA_RE = re.compile(r"\.\s*,\s*")
COMMA_DOT_RE = re.compile(r",\s*\.")
TRAILING_COMMA_RE = re.compile(r",\s*(?=$|\n)")
EXCESS_SPACES_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([,.!?;:])")
EXCESS_SPACES_AFTER_PUNCT_RE = re.compile(r"([,.!?;:])[ \t]{2,}")
MULTI_SPACES_RE = re.compile(r"[ \t]{2,}")
LIA_PATTERN = re.compile(r"\bLIA\b")
SENTENCE_END_RE = re.compile(r"([.!?…]+)")
SPEAKER_LABEL_RE = re.compile(r"^(?P<label>(?:\*\*)?SPEAKER_\d+(?:\*\*)?\s*:\s*)(?P<body>.*)$")
CODE_FENCE_RE = re.compile(r"^\s*```")
DEFAULT_GLOSSARY: Dict[str, str] = {
    "LIA": "l’IA",
    "Lia": "l’IA",
    "Lya": "l’IA",
    "Shrime Jesus": "Shrimp Jesus",
}


def fix_punctuation(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    text = DOT_COMMA_RE.sub(". ", text)
    text = COMMA_DOT_RE.sub(".", text)
    text = TRAILING_COMMA_RE.sub("", text)
    text = PUNCT_DUPLICATES_RE.sub(r"\1", text)
    text = EXCESS_SPACES_BEFORE_PUNCT_RE.sub(r" \1", text)
    text = EXCESS_SPACES_AFTER_PUNCT_RE.sub(r"\1 ", text)
    text = MULTI_SPACES_RE.sub(" ", text)
    return text.strip(" ") if text else text


def fix_lia(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    return LIA_PATTERN.sub("l’IA", text)


def dedupe_local_repeats(text: str, max_ngram: int = 8) -> str:
    if not isinstance(text, str) or not text or max_ngram < 2:
        return text

    def process_sentence(sentence: str) -> str:
        tokens = sentence.split()
        n = len(tokens)
        i = 0
        while i < n:
            matched = False
            for k in range(min(max_ngram, (n - i) // 2), 1, -1):
                a = tokens[i : i + k]
                b = tokens[i + k : i + 2 * k]
                if not b:
                    continue
                if [t.lower() for t in a] == [t.lower() for t in b]:
                    del tokens[i + k : i + 2 * k]
                    n = len(tokens)
                    matched = True
                    break
            if not matched:
                i += 1
        return " ".join(tokens)

    parts = SENTENCE_END_RE.split(text)
    rebuilt = []
    for idx in range(0, len(parts), 2):
        chunk = parts[idx]
        sep = parts[idx + 1] if idx + 1 < len(parts) else ""
        if chunk.strip():
            chunk = process_sentence(chunk)
        rebuilt.append(chunk + sep)
    return "".join(rebuilt)


def apply_replacement_glossary(text: str, glossary: Optional[Dict[str, str]] = None) -> str:
    if not glossary or not text:
        return text
    result = text
    for source, target in glossary.items():
        if not source or target is None:
            continue
        pattern = re.compile(rf"\b{re.escape(source)}\b", flags=re.IGNORECASE)
        result = pattern.sub(str(target), result)
    return result


def clean_human_text(
    text: str,
    *,
    dedupe: bool = True,
    max_ngram: int = 8,
    glossary: Optional[Dict[str, str]] = None,
) -> str:
    if not isinstance(text, str) or not text:
        return text
    cleaned = text.strip()
    cleaned = fix_punctuation(cleaned)
    if dedupe:
        cleaned = dedupe_local_repeats(cleaned, max_ngram=max_ngram)
    cleaned = fix_lia(cleaned)
    cleaned = apply_replacement_glossary(cleaned, glossary)
    return cleaned


def _strip_markdown_prefix(text: str) -> Tuple[str, str, bool]:
    if not text:
        return "", "", False
    prefix = ""
    remainder = text
    skip_dedupe = False
    leading_ws = len(remainder) - len(remainder.lstrip())
    if leading_ws:
        prefix += remainder[:leading_ws]
        remainder = remainder[leading_ws:]
    blockquote_match = re.match(r"((?:>\s*)+)(.*)", remainder)
    if blockquote_match:
        prefix += blockquote_match.group(1)
        remainder = blockquote_match.group(2)
    heading_match = re.match(r"(#{1,6}\s+)(.*)", remainder)
    if heading_match:
        prefix += heading_match.group(1)
        remainder = heading_match.group(2)
        skip_dedupe = True
        return prefix, remainder, skip_dedupe
    list_match = re.match(r"((?:[-*+])\s+)(.*)", remainder)
    if list_match:
        prefix += list_match.group(1)
        remainder = list_match.group(2)
    else:
        numbered_match = re.match(r"((?:\d+\.)\s+)(.*)", remainder)
        if numbered_match:
            prefix += numbered_match.group(1)
            remainder = numbered_match.group(2)
    return prefix, remainder, skip_dedupe


def _split_speaker_label(text: str) -> Tuple[str, str]:
    if not text:
        return "", ""
    match = SPEAKER_LABEL_RE.match(text)
    if not match:
        return "", text
    return match.group("label"), match.group("body")


def _strip_orphan_comma(text: str) -> str:
    return re.sub(r"^\s*,\s*", "", text)


def normalize_markdown_line(line: str, glossary: Optional[Dict[str, str]] = None) -> str:
    if not line:
        return line
    stripped = line.lstrip()
    if CODE_FENCE_RE.match(stripped):
        return line
    prefix, remainder, skip_dedupe = _strip_markdown_prefix(line)
    if not remainder:
        return prefix
    speaker_label, body = _split_speaker_label(remainder)
    if speaker_label:
        body = _strip_orphan_comma(body)
    cleaned = clean_human_text(body, dedupe=not skip_dedupe, glossary=glossary)
    return f"{prefix}{speaker_label}{cleaned}" if (speaker_label or prefix) else cleaned


def normalize_markdown_block(text: str, glossary: Optional[Dict[str, str]] = None) -> str:
    if not text:
        return text
    lines = text.splitlines()
    normalized = [normalize_markdown_line(line, glossary=glossary) for line in lines]
    return "\n".join(normalized)

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

from text_cleaning import apply_replacement_glossary, clean_human_text, dedupe_local_repeats

from .models import Phrase

SPEAKER_RE = re.compile(r"^(?P<label>[A-Za-z][\w .'-]{0,30}?|SPEAKER_\d{2})(?:\s*):\s+(?P<body>.*)$")
TIMESTAMP_RE = re.compile(r"\[?\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\]?")
COMPACT_TIMESTAMP_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
TAG_RE = re.compile(r"<[^>]+>")
BRACKET_RE = re.compile(r"\[[^\]]+\]")
PAREN_MARKER_RE = re.compile(r"\([^)]*\)")


class EditorialNormalizer:
    def __init__(self, config: Dict):
        self.cfg = config or {}
        glossary_cfg = self.cfg.get("glossary") or {}
        self.glossary_map = self._build_glossary_map(glossary_cfg)
        self.replacements = self._compile_replacements(self.cfg.get("replacements", []))
        markers = self.cfg.get("technical_markers") or []
        self.marker_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in markers if pattern]

    def run(self, lines: Sequence[str]) -> Tuple[List[Phrase], Dict[str, int]]:
        phrases: List[Phrase] = []
        modified = 0
        dropped = 0
        for idx, raw in enumerate(lines):
            speaker, body = self._split_speaker(raw.strip())
            normalized = self._normalize_text(body)
            is_dropped = False
            if not normalized:
                is_dropped = True
                dropped += 1
            if normalized != body:
                modified += 1
            phrase = Phrase(
                index=idx,
                speaker=speaker,
                raw_text=raw,
                text=normalized,
                changed=normalized != body,
                dropped=is_dropped,
            )
            phrases.append(phrase)
        stats = {"modified_lines": modified, "dropped_lines": dropped, "total_lines": len(lines)}
        return phrases, stats

    def _split_speaker(self, line: str) -> Tuple[str, str]:
        match = SPEAKER_RE.match(line)
        if not match:
            return "", line
        speaker = match.group("label").strip()
        body = match.group("body").strip()
        return speaker, body

    def _normalize_text(self, text: str) -> str:
        cleaned = text.strip()
        if self.cfg.get("remove_timestamps", True):
            cleaned = TIMESTAMP_RE.sub(" ", cleaned)
            cleaned = COMPACT_TIMESTAMP_RE.sub(" ", cleaned)
        if self.cfg.get("strip_internal_tags", True):
            cleaned = TAG_RE.sub(" ", cleaned)
        if self.cfg.get("strip_bracketed_markers", True):
            cleaned = BRACKET_RE.sub(" ", cleaned)
            cleaned = PAREN_MARKER_RE.sub(" ", cleaned)
        for pattern in self.marker_patterns:
            cleaned = pattern.sub(" ", cleaned)

        for pattern, replacement in self.replacements:
            cleaned = pattern.sub(replacement, cleaned)

        if self.cfg.get("fix_punctuation", True):
            cleaned = clean_human_text(cleaned, dedupe=False)

        if self.cfg.get("dedupe_repeated_words", True):
            cleaned = dedupe_local_repeats(cleaned, max_ngram=6)

        cleaned = apply_replacement_glossary(cleaned, glossary=self.glossary_map)

        if self.cfg.get("collapse_whitespace", True):
            cleaned = re.sub(r"\s{2,}", " ", cleaned)

        return cleaned.strip()

    def _build_glossary_map(self, glossary_cfg: Dict) -> Dict[str, str]:
        mapping: Dict[str, str] = {}
        for entry in glossary_cfg.get("canonical_forms", []):
            if not entry:
                continue
            mapping[entry] = entry
        for replacement in glossary_cfg.get("replacements", []):
            source = (replacement or {}).get("source")
            target = (replacement or {}).get("target")
            if source and target:
                mapping[source] = target
        return mapping

    def _compile_replacements(self, replacements: List[Dict]) -> List[Tuple[re.Pattern, str]]:
        compiled: List[Tuple[re.Pattern, str]] = []
        for entry in replacements or []:
            pattern = entry if isinstance(entry, str) else entry.get("pattern")
            replacement = entry.get("replacement") if isinstance(entry, dict) else ""
            if not pattern:
                continue
            compiled.append((re.compile(pattern), replacement))
        return compiled

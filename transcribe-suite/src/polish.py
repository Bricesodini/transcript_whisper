
from __future__ import annotations

import re
from typing import Any, Dict, List, Set, Tuple

NBSP = "\u00A0"


def _sentence_split(text: str, max_words: int) -> str:
    words = text.split()
    if len(words) <= max_words:
        return text
    out: List[str] = []
    buf: List[str] = []
    for w in words:
        buf.append(w)
        if len(buf) >= max_words and re.search(r"[.!?…]$", w):
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    return " ".join(out)


def _normalize_ellipsis(text: str) -> str:
    return re.sub(r"\.\.\.", "…", text)


def _normalize_quotes_fr(text: str) -> str:
    def repl(match: re.Match) -> str:
        inner = match.group(1).strip()
        return f" « {inner} » "

    return re.sub(r'"([^"]+)"', repl, text)


def _ensure_terminal_punct(text: str) -> str:
    stripped = text.rstrip()
    return stripped if re.search(r"[.!?…]$", stripped) else stripped + "."


def _capitalize_start(text: str) -> str:
    stripped = text.lstrip()
    return stripped[:1].upper() + stripped[1:] if stripped else stripped


def _capitalize_word(word: str) -> str:
    return word[:1].upper() + word[1:] if word else word


def _apply_replacements(text: str, replacements: List[List[str]]) -> str:
    updated = text
    for src, dst in replacements:
        updated = re.sub(re.escape(src), dst, updated, flags=re.IGNORECASE)
    return updated


def _should_lower_word(word: str, whitelist: Set[str]) -> bool:
    if not word:
        return False
    core = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", word)
    if not core or len(core) == 1:
        return False
    upper_core = core.upper()
    if upper_core in whitelist:
        return False
    if core.isupper():
        return False
    if not core[0].isupper():
        return False
    return core[1:].islower()


def _apply_sentence_case(text: str, whitelist: Set[str]) -> str:
    if not text:
        return text

    word_pattern = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ’'_-]+")
    result: List[str] = []
    last_idx = 0
    start_sentence = True

    for match in word_pattern.finditer(text):
        separator = text[last_idx : match.start()]
        if separator:
            result.append(separator)
        if separator and re.search(r"[.!?…]", separator):
            start_sentence = True

        word = match.group(0)
        if start_sentence:
            result.append(_capitalize_word(word))
            start_sentence = False
        else:
            result.append(word.lower() if _should_lower_word(word, whitelist) else word)

        last_idx = match.end()

    result.append(text[last_idx:])
    return "".join(result)


def _apply_nbsp_fr(text: str, before: List[str], after: List[str]) -> str:
    if not text:
        return text
    for symbol in before:
        escaped = re.escape(symbol)
        text = re.sub(rf"[ \t{NBSP}]*{escaped}", f"{NBSP}{symbol}", text)
    for symbol in after:
        escaped = re.escape(symbol)
        text = re.sub(rf"{escaped}[ \t{NBSP}]*", f"{symbol}{NBSP}", text)
    return text


def _normalize_list_markers(text: str, bullet: str) -> str:
    if not text or not bullet:
        return text
    pattern = re.compile(r"(^|\n)[ \t]*-\s+")
    return pattern.sub(lambda match: f"{match.group(1)}{bullet} ", text)


class Polisher:
    def __init__(self, cfg: Dict[str, Any], logger):
        self.cfg = cfg or {}
        self.logger = logger
        self.lexicon_rules = self._compile_lexicon(self.cfg.get("lexicon", []))

    def run(self, segments: List[Dict[str, Any]], lang: str = "fr") -> List[Dict[str, Any]]:
        if not segments or not self.cfg.get("enabled", False):
            return segments

        max_words = int(self.cfg.get("max_sentence_words", 18))
        join_gap_ms = int(self.cfg.get("join_short_segments_ms", 650))
        replacements = self.cfg.get("replacements", [])
        norm_ellipsis = bool(self.cfg.get("normalize_ellipses", True))
        norm_quotes = bool(self.cfg.get("normalize_quotes", True))
        ensure_punct = bool(self.cfg.get("ensure_terminal_punct", True))
        sentence_case = bool(self.cfg.get("sentence_case", False))
        acronym_whitelist = {item.upper() for item in self.cfg.get("acronym_whitelist", [])}
        enable_nbsp = bool(self.cfg.get("enable_nbsp", True))
        fr_nbsp_before = self.cfg.get("fr_nbsp_before", [])
        fr_nbsp_after = self.cfg.get("fr_nbsp_after", [])
        normalize_lists = bool(self.cfg.get("normalize_list_markers", True))
        list_bullet_symbol = self.cfg.get("list_bullet_symbol", "•")

        merged: List[Dict[str, Any]] = []
        for seg in segments:
            if (
                merged
                and (seg["start"] - merged[-1]["end"]) * 1000 <= join_gap_ms
                and seg["speaker"] == merged[-1].get("speaker")
            ):
                merged[-1]["text"] = (merged[-1]["text"].rstrip() + " " + seg["text"].lstrip()).strip()
                merged[-1]["end"] = seg["end"]
                merged[-1].setdefault("words", []).extend(seg.get("words", []))
            else:
                merged.append(dict(seg))

        for seg in merged:
            text = seg["text"].strip()
            if replacements:
                text = _apply_replacements(text, replacements)
            if self.lexicon_rules:
                text = self._apply_lexicon(text)
            if norm_ellipsis:
                text = _normalize_ellipsis(text)
            if sentence_case:
                text = _apply_sentence_case(text, acronym_whitelist)
            else:
                text = _capitalize_start(text)
            if ensure_punct:
                text = _ensure_terminal_punct(text)
            if norm_quotes and lang.startswith("fr"):
                text = _normalize_quotes_fr(text)
            if enable_nbsp and lang.startswith("fr") and (fr_nbsp_before or fr_nbsp_after):
                text = _apply_nbsp_fr(text, fr_nbsp_before, fr_nbsp_after)
            if normalize_lists and list_bullet_symbol and lang.startswith("fr"):
                text = _normalize_list_markers(text, str(list_bullet_symbol))
            text = _sentence_split(text, max_words)
            seg["text"] = text.strip()

        self.logger.info("Polish: sentences<=%dw, join<=%dms appliqués (%d segments)", max_words, join_gap_ms, len(merged))
        return merged

    def _compile_lexicon(self, entries: List[Any]) -> List[Tuple[re.Pattern, str]]:
        compiled: List[Tuple[re.Pattern, str]] = []
        for entry in entries or []:
            pattern = None
            replacement = ""
            flags = 0
            case_insensitive = True
            if isinstance(entry, dict):
                pattern = entry.get("pattern")
                replacement = entry.get("replacement", "")
                case_insensitive = entry.get("case_insensitive", True)
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                pattern, replacement = entry[0], entry[1]
            else:
                continue
            if not pattern:
                continue
            if case_insensitive:
                flags |= re.IGNORECASE
            try:
                compiled.append((re.compile(pattern, flags), str(replacement)))
            except re.error as exc:
                self.logger.warning("Lexicon pattern invalide '%s': %s", pattern, exc)
        return compiled

    def _apply_lexicon(self, text: str) -> str:
        updated = text
        for pattern, replacement in self.lexicon_rules:
            updated = pattern.sub(replacement, updated)
        return updated


from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from glossary import GlossaryManager
from textnorm import TextNormalizer, join_text

NBSP = "\u00A0"
_FRENCH_PUNCT_GAP = re.compile(r"([.!?])([A-Za-z\u00C0-\u017F])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
LEADING_GUARDS = set('«»"\'“”‚‘„’()[]{}—–-•·¶®¶¯')


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


def _capitalize_word(word: str) -> str:
    return word[:1].upper() + word[1:] if word else word


def _fix_french_spacing(text: str) -> str:
    if not text:
        return text
    updated = _FRENCH_PUNCT_GAP.sub(r"\1 \2", text)
    return _MULTI_SPACE.sub(" ", updated)


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


def _apply_sentence_case(
    text: str,
    whitelist: Set[str],
    canonical_map: Dict[str, str],
    punctuation_cfg: Dict[str, List[str]],
) -> str:
    if not text:
        return text

    word_pattern = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ’'_-]+")
    result: List[str] = []
    last_idx = 0
    start_sentence = True
    lower_next_soft = False
    quote_guard = False
    reset_tokens = punctuation_cfg.get("reset_after", [".", "!", "?", "…"])
    soft_tokens = punctuation_cfg.get("soft_after", [",", ";", ":", " :"])

    for match in word_pattern.finditer(text):
        separator = text[last_idx : match.start()]
        if separator:
            result.append(separator)
        if separator:
            stripped = separator.replace(NBSP, " ").rstrip()
            separator_has_quote = bool(re.search(r'[\"\'«»]', separator))
            stripped = stripped.rstrip('»"\' ')
            if stripped and any(stripped.endswith(token) for token in reset_tokens):
                start_sentence = True
                lower_next_soft = False
                quote_guard = False
            elif stripped and any(stripped.endswith(token) for token in soft_tokens):
                lower_next_soft = True
                quote_guard = separator_has_quote

        word = match.group(0)
        upper_word = word.upper()
        canonical = canonical_map.get(upper_word)
        if start_sentence:
            replacement = canonical or _capitalize_word(word)
            result.append(replacement)
            start_sentence = False
            lower_next_soft = False
        else:
            if canonical:
                result.append(canonical)
                lower_next_soft = False
                quote_guard = False
            elif lower_next_soft:
                if quote_guard:
                    result.append(word)
                elif _soft_lower_allowed(word):
                    result.append(word.lower())
                else:
                    result.append(word)
                lower_next_soft = False
                quote_guard = False
            else:
                result.append(word.lower() if _should_lower_word(word, whitelist) else word)

        last_idx = match.end()

    result.append(text[last_idx:])
    return "".join(result)


def _soft_lower_allowed(word: str) -> bool:
    if not word:
        return False
    leading = word.lstrip(' "\'«»')
    if not leading:
        return False
    if word[0] in {'"', "'", "«", "“"}:
        return False
    return True


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


def _capitalize_leading(text: str) -> str:
    if not text:
        return text
    stripped = text.lstrip()
    if not stripped:
        return text
    lead_ws = len(text) - len(stripped)
    idx = 0
    guards = LEADING_GUARDS | {NBSP}
    while idx < len(stripped) and stripped[idx] in guards:
        idx += 1
    while idx < len(stripped) and stripped[idx].isspace():
        idx += 1
    if idx >= len(stripped):
        return text
    prefix = stripped[:idx]
    remainder = stripped[idx:]
    capitalized = prefix + _capitalize_word(remainder)
    return text[:lead_ws] + capitalized


class Polisher:
    def __init__(
        self,
        cfg: Dict[str, Any],
        logger,
        glossary: Optional[GlossaryManager] = None,
        numbers_cfg: Optional[Dict[str, Any]] = None,
        typography_cfg: Optional[Dict[str, Any]] = None,
    ):
        self.cfg = cfg or {}
        self.logger = logger
        self.glossary = glossary
        self.normalizer = TextNormalizer(numbers_cfg or {}, typography_cfg or {})
        self.lexicon_rules = self._compile_lexicon(self.cfg.get("lexicon", []))
        self.strip_oral_markers = bool(self.cfg.get("strip_oral_markers", True))
        oral_markers = self.cfg.get("oral_markers")
        if oral_markers is None:
            oral_markers = ["tu vois", "tu sais", "on va dire que", "genre", "voilà", "bref"]
        self.oral_marker_patterns = (
            [self._compile_marker(marker) for marker in oral_markers if marker.strip()] if self.strip_oral_markers else []
        )
        punct_cfg = self.cfg.get("punctuation", {})
        self.punctuation_cfg = {
            "reset_after": punct_cfg.get("reset_after", [".", "!", "?", "…"]),
            "soft_after": punct_cfg.get("soft_after", [",", ";", ":", " :"]),
        }
        self.fix_french_spacing = bool(self.cfg.get("fix_french_spacing", True))
        self.audit_sample_size = max(1, int(self.cfg.get("audit_sample_size", 5)))
        self._report: Dict[str, Any] = {}

    def run(self, segments: List[Dict[str, Any]], lang: str = "fr") -> List[Dict[str, Any]]:
        if not segments or not self.cfg.get("enabled", False):
            return segments

        max_words = int(self.cfg.get("max_sentence_words", 18))
        join_gap_ms = int(self.cfg.get("join_short_segments_ms", 650))
        replacements = self.cfg.get("replacements", [])
        norm_ellipsis = bool(self.cfg.get("normalize_ellipses", False))
        norm_quotes = bool(self.cfg.get("normalize_quotes", True))
        ensure_punct = bool(self.cfg.get("ensure_terminal_punct", True))
        sentence_case = bool(self.cfg.get("sentence_case", False))
        acronym_whitelist = {item.upper() for item in self.cfg.get("acronym_whitelist", [])}
        enable_nbsp = bool(self.cfg.get("enable_nbsp", True))
        fr_nbsp_before = self.cfg.get("fr_nbsp_before", [])
        fr_nbsp_after = self.cfg.get("fr_nbsp_after", [])
        normalize_lists = bool(self.cfg.get("normalize_list_markers", True))
        list_bullet_symbol = self.cfg.get("list_bullet_symbol", "•")

        self._init_report(len(segments))
        merged: List[Dict[str, Any]] = []
        for seg in segments:
            seg_copy = dict(seg)
            seg_copy.setdefault("text_fragments", [seg_copy.get("text", "")])
            if (
                merged
                and (seg["start"] - merged[-1]["end"]) * 1000 <= join_gap_ms
                and seg["speaker"] == merged[-1].get("speaker")
            ):
                merged[-1].setdefault("text_fragments", []).extend(seg_copy.get("text_fragments", [seg_copy.get("text")]))
                merged[-1]["text"] = join_text(merged[-1]["text_fragments"])
                merged[-1]["end"] = seg["end"]
                merged[-1].setdefault("words", []).extend(seg.get("words", []))
                self._report["joined_segments"] += 1
            else:
                merged.append(seg_copy)

        glossary_whitelist, glossary_map = self._glossary_terms()
        acronym_whitelist = {item.upper() for item in self.cfg.get("acronym_whitelist", [])}
        canonical_map: Dict[str, str] = {item: item for item in acronym_whitelist}
        canonical_map.update(glossary_map)
        combined_whitelist = acronym_whitelist | glossary_whitelist

        for seg in merged:
            language = seg.get("language") or lang
            fragments = seg.get("text_fragments") or [seg.get("text")]
            text = join_text(fragments).strip()
            if self.strip_oral_markers and self.oral_marker_patterns:
                text, marker_hits = self._strip_oral_markers(text)
                if marker_hits:
                    self._report["oral_markers_removed"] += marker_hits
                    text = _capitalize_leading(text)
            if replacements:
                text = _apply_replacements(text, replacements)
            if self.lexicon_rules:
                text = self._apply_lexicon(text)
            if norm_ellipsis:
                text = _normalize_ellipsis(text)
            if sentence_case:
                text = _apply_sentence_case(text, combined_whitelist, canonical_map, self.punctuation_cfg)
            if ensure_punct:
                text = _ensure_terminal_punct(text)
            if self.fix_french_spacing and language.startswith("fr"):
                text = _fix_french_spacing(text)
            if norm_quotes and language.startswith("fr"):
                text = _normalize_quotes_fr(text)
            if enable_nbsp and language.startswith("fr") and (fr_nbsp_before or fr_nbsp_after):
                text = _apply_nbsp_fr(text, fr_nbsp_before, fr_nbsp_after)
            if normalize_lists and list_bullet_symbol and language.startswith("fr"):
                text = _normalize_list_markers(text, str(list_bullet_symbol))
            text, split_count = self._sentence_split(text, max_words)
            if split_count:
                self._report["sentence_splits"] += split_count
            final_text = text.strip()
            seg["text"] = final_text
            seg["text_human"] = final_text
            seg["text_machine"] = self.normalizer.normalize_machine(final_text, language)
            seg["language"] = language
            seg.pop("text_fragments", None)

        self._report["output_segments"] = len(merged)
        self.logger.info("Polish: sentences<=%dw, join<=%dms appliqués (%d segments)", max_words, join_gap_ms, len(merged))
        return merged

    def _init_report(self, total: int) -> None:
        self._report = {
            "input_segments": total,
            "output_segments": total,
            "joined_segments": 0,
            "oral_markers_removed": 0,
            "sentence_splits": 0,
        }

    def _strip_oral_markers(self, text: str) -> Tuple[str, int]:
        total = 0
        updated = text
        for pattern in self.oral_marker_patterns:
            updated, hits = pattern.subn(" ", updated)
            total += hits
        return updated, total

    def _glossary_terms(self) -> Tuple[Set[str], Dict[str, str]]:
        if not self.glossary:
            return set(), {}
        whitelist: Set[str] = set()
        canonical: Dict[str, str] = {}
        for entry in self.glossary.snapshot():
            for token in entry.split():
                cleaned = token.strip()
                if not cleaned:
                    continue
                upper = cleaned.upper()
                whitelist.add(upper)
                canonical.setdefault(upper, cleaned)
        return whitelist, canonical

    def _sentence_split(self, text: str, max_words: int) -> Tuple[str, int]:
        words = text.split()
        if len(words) <= max_words:
            return text, 0
        sentences: List[str] = []
        buffer: List[str] = []
        for word in words:
            buffer.append(word)
            if len(buffer) >= max_words and re.search(r"[.!?…]$", word):
                sentences.append(" ".join(buffer))
                buffer = []
        if buffer:
            sentences.append(" ".join(buffer))
        splits = max(0, len(sentences) - 1)
        return " ".join(sentences), splits

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

    def _compile_marker(self, marker: str) -> re.Pattern:
        escaped = re.escape(marker.strip())
        escaped = re.sub(r"\\\s+", r"\\s+", escaped)
        return re.compile(rf"(?i)\b{escaped}\b")

    def report(self) -> Dict[str, Any]:
        return dict(self._report)

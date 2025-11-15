import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set


ENTITY_PATTERN = re.compile(r"\b([A-ZÀ-ÖØ-Þ][\w'’\-]+(?:\s+[A-ZÀ-ÖØ-Þ][\w'’\-]+)*)\b")


class GlossaryManager:
    """Tracks named entities / specific terms to avoid unwanted lowercasing."""

    def __init__(self, cfg: Optional[Dict[str, Any]] = None, logger=None, config_root: Optional[Path] = None):
        self.logger = logger
        self.cfg = cfg or {}
        self.config_root = Path(config_root) if config_root else None
        self.dynamic = bool(self.cfg.get("dynamic", True))
        self.min_length = int(self.cfg.get("min_length", 3))
        self.stopwords = {self._normalize(word) for word in self.cfg.get("stopwords", []) if word}
        self.mode = str(self.cfg.get("mode", "lenient")).lower()
        self.conflicts: List[Dict[str, str]] = []
        self.protected_terms: Set[str] = set()
        self.preferred_map: Dict[str, str] = {}
        self.forbidden_terms: Set[str] = set()
        self._entries: Dict[str, str] = {}
        for term in self.cfg.get("entries", []):
            self.add(term)
        self._load_external_sources()

    def _normalize(self, value: str) -> str:
        normalized = unicodedata.normalize("NFC", value or "")
        normalized = normalized.strip()
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized.lower()

    def add(self, term: str) -> None:
        if not term:
            return
        normalized = self._normalize(term)
        if len(normalized) < self.min_length:
            return
        if normalized in self.stopwords:
            return
        self._entries[normalized] = term.strip()

    def ingest(self, text: str, tokens: Optional[Iterable[str]] = None) -> None:
        if not self.dynamic or not text:
            return
        for candidate in self._extract_candidates(text):
            self.add(candidate)
        if not tokens:
            return
        for token in tokens:
            if not token:
                continue
            cleaned = token.strip()
            if len(cleaned) < self.min_length:
                continue
            if any(char.isdigit() for char in cleaned):
                self.add(cleaned)

    def _extract_candidates(self, text: str) -> List[str]:
        candidates: List[str] = []
        for match in ENTITY_PATTERN.finditer(text):
            value = match.group(1).strip()
            if len(value) < self.min_length:
                continue
            normalized = self._normalize(value)
            if normalized in self.stopwords:
                continue
            candidates.append(value)
        return candidates

    def should_preserve(self, word: str) -> bool:
        if not word:
            return False
        normalized = self._normalize(word)
        if normalized in self.protected_terms:
            return True
        return normalized in self._entries

    def canonical(self, word: str) -> Optional[str]:
        if not word:
            return None
        normalized = self._normalize(word)
        preferred = self.preferred_map.get(normalized)
        if preferred:
            if normalized in self.protected_terms and self.mode == "strict":
                self._register_conflict(word, preferred)
            else:
                return preferred
        return self._entries.get(normalized)

    def snapshot(self) -> List[str]:
        items = set(self._entries.values())
        items.update(self.protected_terms)
        return sorted(items)

    def extend_whitelist(self, whitelist: Set[str]) -> Set[str]:
        if not self._entries:
            return whitelist
        extended = set(whitelist)
        for term in self._entries.values():
            extended.add(term.upper())
        for term in self.protected_terms:
            extended.add(term.upper())
        return extended

    def conflicts_summary(self) -> List[Dict[str, str]]:
        return list(self.conflicts)

    # ---- internal helpers ----
    def _load_external_sources(self) -> None:
        self._load_word_list(self.cfg.get("protected", []), self.protected_terms)
        self._load_word_file(self.cfg.get("protected_file"), self.protected_terms)
        self._load_pairs(self.cfg.get("preferred", []))
        self._load_pairs_file(self.cfg.get("preferred_file"))
        self._load_word_list(self.cfg.get("forbidden", []), self.forbidden_terms)
        self._load_word_file(self.cfg.get("forbidden_file"), self.forbidden_terms)

    def _load_word_list(self, entries: Iterable[str], target: Set[str]) -> None:
        for entry in entries or []:
            norm = self._normalize(entry)
            if norm:
                target.add(norm)

    def _load_word_file(self, path_value: Optional[str], target: Set[str]) -> None:
        path = self._resolve_path(path_value)
        if not path or not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            norm = self._normalize(raw)
            if norm:
                target.add(norm)

    def _load_pairs(self, entries: Iterable[Any]) -> None:
        for entry in entries or []:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                src, dst = entry[0], entry[1]
            elif isinstance(entry, str) and "\t" in entry:
                src, dst = entry.split("\t", 1)
            else:
                continue
            norm = self._normalize(src)
            if norm:
                self.preferred_map[norm] = dst.strip()

    def _load_pairs_file(self, path_value: Optional[str]) -> None:
        path = self._resolve_path(path_value)
        if not path or not path.exists():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "\t" not in raw:
                continue
            self._load_pairs([raw])

    def _resolve_path(self, value: Optional[str]) -> Optional[Path]:
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute() and self.config_root:
            path = self.config_root / path
        return path

    def _register_conflict(self, word: str, preferred: str) -> None:
        conflict = {"word": word, "preferred": preferred}
        self.conflicts.append(conflict)
        if self.logger:
            self.logger.warning("Glossary conflit (strict): %s vs %s", word, preferred)

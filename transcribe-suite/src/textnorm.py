import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Tuple

NUMBER_WORD_PATTERN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+(?:-[A-Za-zÀ-ÖØ-öø-ÿ]+)*")

FR_BASE = {
    "zéro": 0,
    "zero": 0,
    "un": 1,
    "une": 1,
    "deux": 2,
    "trois": 3,
    "quatre": 4,
    "cinq": 5,
    "six": 6,
    "sept": 7,
    "huit": 8,
    "neuf": 9,
    "dix": 10,
    "onze": 11,
    "douze": 12,
    "treize": 13,
    "quatorze": 14,
    "quinze": 15,
    "seize": 16,
    "dix sept": 17,
    "dix-huit": 18,
    "dix neuf": 19,
    "vingt": 20,
}

FR_TENS = {
    "vingt": 20,
    "trente": 30,
    "quarante": 40,
    "cinquante": 50,
    "soixante": 60,
    "soixante dix": 70,
    "soixante-dix": 70,
    "quatre vingt": 80,
    "quatre-vingt": 80,
    "quatre-vingts": 80,
    "quatre vingt dix": 90,
    "quatre-vingt-dix": 90,
}

EN_BASE = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

EN_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}


def _word_number_value(word: str, lang: str) -> Optional[int]:
    normalized = unicodedata.normalize("NFC", word or "")
    normalized = normalized.replace("-", " ")
    normalized = normalized.replace("’", " ")
    normalized = normalized.replace("'", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip().lower()
    if not normalized:
        return None
    if lang.startswith("fr"):
        base = FR_BASE
        tens = FR_TENS
    else:
        base = EN_BASE
        tens = EN_TENS
    if normalized in base:
        return base[normalized]
    parts = normalized.split()
    if len(parts) == 2 and parts[0] in tens and parts[1] in base and base[parts[1]] < 10:
        return tens[parts[0]] + base[parts[1]]
    return None


class TextNormalizer:
    """Produces human vs machine variants of text."""

    def __init__(self, numbers_cfg: Optional[Dict] = None, typography_cfg: Optional[Dict] = None):
        numbers_cfg = numbers_cfg or {}
        self.human_numbers = bool(numbers_cfg.get("human_numbers", True))
        whitelist = numbers_cfg.get("whitelist_patterns", []) or []
        self.number_whitelist: List[re.Pattern] = []
        for pattern in whitelist:
            try:
                self.number_whitelist.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                continue

        typography_cfg = typography_cfg or {}
        self.locale = str(typography_cfg.get("locale", "auto")).lower()

    def normalize_pair(self, text: str, language: str) -> Tuple[str, str]:
        human = self.normalize_human(text, language)
        machine = self.normalize_machine(human, language)
        return human, machine

    def normalize_human(self, text: str, language: Optional[str] = None) -> str:
        normalized = unicodedata.normalize("NFC", text)
        normalized = self._normalize_numbers(normalized, language_hint=language, for_machine=False)
        normalized = re.sub(r"\s{2,}", " ", normalized)
        return normalized.strip()

    def normalize_machine(self, text: str, language: str) -> str:
        normalized = unicodedata.normalize("NFKC", text or "")
        normalized = self._normalize_numbers(normalized, language, for_machine=True)
        normalized = self._strip_accents(normalized)
        normalized = normalized.replace("\u00A0", " ")
        normalized = re.sub(r"[“”«»]", '"', normalized)
        normalized = re.sub(r"[^A-Za-z0-9 ,.;:!?'\-\n]", " ", normalized)
        normalized = re.sub(r"\s{2,}", " ", normalized)
        return normalized.strip()

    def _normalize_numbers(self, text: str, language_hint: Optional[str], for_machine: bool) -> str:
        def repl(match: re.Match) -> str:
            token = match.group(0)
            if self._matches_whitelist(token):
                return token
            if not for_machine and self.human_numbers:
                return token
            lang = language_hint or "fr"
            value = _word_number_value(token, lang)
            if value is None:
                return token
            return str(value)

        return NUMBER_WORD_PATTERN.sub(repl, text)

    def _matches_whitelist(self, token: str) -> bool:
        if not token:
            return False
        for pattern in self.number_whitelist:
            if pattern.search(token):
                return True
        return False

    def _strip_accents(self, text: str) -> str:
        decomposed = unicodedata.normalize("NFKD", text)
        return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def join_text(values: Iterable[str]) -> str:
    parts = [value.strip() for value in values if value and value.strip()]
    if not parts:
        return ""
    return " ".join(parts)

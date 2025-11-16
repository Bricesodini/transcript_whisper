from __future__ import annotations

import re
from typing import Dict, List, Sequence

from .models import Phrase, QAResult


DOUBLE_SPACE_RE = re.compile(r" {2,}")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+[;:!?]")


class QAReporter:
    def __init__(self, cfg: Dict):
        self.cfg = cfg or {}

    def run(
        self,
        phrases: Sequence[Phrase],
        normalized_stats: Dict[str, int],
        low_conf_stats: Dict[str, int],
        final_text: str,
        diagnostics: Dict | None = None,
    ) -> QAResult:
        total = normalized_stats.get("total_lines", len(phrases))
        retained = sum(1 for phrase in phrases if not phrase.dropped)
        modified = normalized_stats.get("modified_lines", 0)
        flagged = sum(1 for phrase in phrases if phrase.flags)
        issues: List[str] = []
        diag_issues = (diagnostics or {}).get("issues") or []
        issues.extend(diag_issues)

        text = final_text or ""
        if self.cfg.get("check_double_spaces", True) and DOUBLE_SPACE_RE.search(text):
            issues.append("Espaces doubles détectés dans la version finale.")
        if self.cfg.get("check_space_before_punct", True) and SPACE_BEFORE_PUNCT_RE.search(text):
            issues.append("Espaces avant la ponctuation forte détectés.")

        for forbidden in self.cfg.get("enforce_glossary_sources") or []:
            if not forbidden:
                continue
            pattern = re.compile(rf"\b{re.escape(forbidden)}\b", re.IGNORECASE)
            if pattern.search(text):
                issues.append(f"Forme non canonique détectée: {forbidden}")

        allow_drop = bool(self.cfg.get("allow_empty_line_drop", False))
        dropped = normalized_stats.get("dropped_lines", 0)
        if dropped and not allow_drop:
            issues.append(f"{dropped} lignes ont été supprimées lors de la normalisation.")

        stats = {
            "dropped_lines": dropped,
            "matched_low_conf": low_conf_stats.get("matched", 0),
        }

        if low_conf_stats.get("orphan"):
            issues.append(f"{low_conf_stats['orphan']} entrées low_confidence non associées.")

        return QAResult(
            total_lines=total,
            retained_lines=retained,
            modified_lines=modified,
            flagged_lines=flagged,
            orphan_low_conf=low_conf_stats.get("orphan", 0),
            sentences_marked=flagged,
            issues=issues,
            stats=stats,
        )


from __future__ import annotations

import difflib
import re
from typing import Dict, List, Tuple

from .models import Phrase


class LowConfidenceAnnotator:
    def __init__(self, cfg: Dict):
        self.cfg = cfg or {}
        self.enabled = bool(self.cfg.get("enabled", True))
        self.threshold = float(self.cfg.get("match_threshold", 0.55))
        self.review_label = self.cfg.get("review_label", "Phrase à vérifier")

    def apply(self, phrases: List[Phrase], entries: List[Dict]) -> Dict[str, int | List[Dict]]:
        if not self.enabled or not entries:
            return {"matched": 0, "orphan": len(entries), "orphan_entries": entries}
        fingerprints = [self._fingerprint(phrase.text) for phrase in phrases]
        matched = 0
        orphan = 0
        orphan_entries: List[Dict] = []
        for entry in entries:
            source = entry.get("text_machine") or entry.get("text") or ""
            fp = self._fingerprint(source)
            idx, score = self._match_phrase(fingerprints, fp)
            if idx is None or score < self.threshold:
                orphan += 1
                orphan_entries.append(entry)
                continue
            phrase = phrases[idx]
            if self.review_label not in phrase.flags:
                phrase.flags.append(self.review_label)
            phrase.low_conf_score = entry.get("score_mean")
            phrase.low_conf_reason = entry.get("reason")
            phrase.extras.setdefault("low_conf_entries", []).append(entry)
            matched += 1
        return {"matched": matched, "orphan": orphan, "orphan_entries": orphan_entries}

    def _match_phrase(self, fingerprints: List[str], target: str) -> Tuple[int | None, float]:
        best_idx = None
        best_score = 0.0
        for idx, candidate in enumerate(fingerprints):
            if not candidate:
                continue
            score = difflib.SequenceMatcher(None, candidate, target).ratio()
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx, best_score

    def _fingerprint(self, text: str) -> str:
        normalized = text or ""
        normalized = normalized.lower()
        normalized = re.sub(r"[^\w]+", "", normalized)
        return normalized

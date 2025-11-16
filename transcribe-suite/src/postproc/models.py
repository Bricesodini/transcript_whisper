from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AssetBundle:
    export_dir: Path
    base_name: str
    clean_txt: Path
    metrics_json: Path
    low_conf_jsonl: Optional[Path] = None
    chapters_json: Optional[Path] = None
    quotes_jsonl: Optional[Path] = None


@dataclass
class Phrase:
    index: int
    speaker: Optional[str]
    raw_text: str
    text: str
    changed: bool = False
    dropped: bool = False
    flags: List[str] = field(default_factory=list)
    annotations: List[str] = field(default_factory=list)
    low_conf_score: Optional[float] = None
    low_conf_reason: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def as_line(self, keep_speaker: bool = True, flag_token: Optional[str] = None) -> str:
        if self.dropped:
            return ""
        prefix = ""
        if keep_speaker and self.speaker:
            prefix = f"{self.speaker}: "
        text = self.text
        if flag_token and self.flags:
            text = f"{flag_token} {text}"
        return f"{prefix}{text}".strip()


@dataclass
class Paragraph:
    items: List[Phrase] = field(default_factory=list)
    speaker: Optional[str] = None

    def add(self, phrase: Phrase) -> None:
        if not self.items:
            self.speaker = phrase.speaker
        self.items.append(phrase)

    def render(self, keep_speaker: bool, flag_token: Optional[str] = None) -> str:
        if not self.items:
            return ""
        if keep_speaker:
            lines = []
            for phrase in self.items:
                line = phrase.as_line(keep_speaker=True, flag_token=flag_token)
                if line:
                    lines.append(line)
            return "\n".join(lines)
        text_chunks = []
        for phrase in self.items:
            chunk = phrase.as_line(keep_speaker=False, flag_token=flag_token)
            if chunk:
                text_chunks.append(chunk)
        return " ".join(text_chunks)


@dataclass
class QAResult:
    total_lines: int
    retained_lines: int
    modified_lines: int
    flagged_lines: int
    orphan_low_conf: int
    sentences_marked: int
    review_labels: List[str] = field(default_factory=list)
    issues: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "total_lines": self.total_lines,
            "retained_lines": self.retained_lines,
            "modified_lines": self.modified_lines,
            "flagged_lines": self.flagged_lines,
            "orphan_low_conf": self.orphan_low_conf,
            "sentences_marked": self.sentences_marked,
            "issues": self.issues,
        }
        if self.review_labels:
            payload["review_labels"] = self.review_labels
        payload.update(self.stats)
        return payload


from __future__ import annotations

from typing import Dict, List

from .models import Paragraph, Phrase


class FinalComposer:
    def __init__(self, cfg: Dict, flag_token: str | None):
        self.cfg = cfg or {}
        self.max_sentences = max(1, int(self.cfg.get("max_sentences", 4)))
        self.max_chars = int(self.cfg.get("max_chars", 800))
        self.keep_speakers = bool(self.cfg.get("keep_speakers", True))
        self.flag_token = flag_token

    def build_paragraphs(self, phrases: List[Phrase]) -> List[Paragraph]:
        paragraphs: List[Paragraph] = []
        current = Paragraph()
        for phrase in phrases:
            if phrase.dropped or not phrase.text:
                continue
            if self._should_split(current, phrase):
                if current.items:
                    paragraphs.append(current)
                current = Paragraph()
            current.add(phrase)
        if current.items:
            paragraphs.append(current)
        return paragraphs

    def _should_split(self, current: Paragraph, phrase: Phrase) -> bool:
        if not current.items:
            return False
        if self.keep_speakers and current.speaker and phrase.speaker and phrase.speaker != current.speaker:
            return True
        if len(current.items) >= self.max_sentences:
            return True
        current_chars = sum(len(item.text) for item in current.items)
        return (current_chars + len(phrase.text)) > self.max_chars

    def render_text(self, paragraphs: List[Paragraph]) -> str:
        blocks = []
        for paragraph in paragraphs:
            text = paragraph.render(self.keep_speakers, flag_token=self.flag_token)
            if text:
                blocks.append(text)
        return "\n\n".join(blocks).strip()


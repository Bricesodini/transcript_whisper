import re
import textwrap
from typing import Dict, List


class Structurer:
    def __init__(self, config: Dict, logger):
        self.logger = logger
        self.cfg = config.get("structure", {})
        self.trim_titles = self.cfg.get("trim_section_titles", False)
        self.title_case = str(self.cfg.get("title_case", "none")).lower()
        self.enable_titles = bool(self.cfg.get("enable_titles", True))
        soft_min = self.cfg.get("soft_min_duration")
        try:
            self.soft_min_duration = float(soft_min) if soft_min else None
        except (TypeError, ValueError):
            self.logger.warning("structure.soft_min_duration invalide, ignoré.")
            self.soft_min_duration = None

    def _new_section(self, index: int):
        return {"index": index, "start": None, "end": None, "segments": [], "text": []}

    def _title_from_text(self, text: str) -> str:
        sentence = re.split(r"(?<=[.!?])\s+", text.strip())
        head = sentence[0] if sentence else text
        return head[:80].strip().rstrip(".") or "Section"

    def _extract_quotes(self, text: str) -> List[str]:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        quotes = []
        for sent in sentences:
            sent = sent.strip()
            if 30 <= len(sent) <= 180:
                quotes.append(sent)
            if len(quotes) >= 3:
                break
        return quotes

    def run(self, segments: List[Dict], language: str) -> Dict:
        if not segments:
            return {"sections": []}

        target = float(self.cfg.get("target_section_duration", 180))
        maximum = float(self.cfg.get("max_section_duration", 480))
        min_gap = float(self.cfg.get("min_pause_gap", 6))

        sections: List[Dict] = []
        section = self._new_section(len(sections))
        last_end = None

        for seg in segments:
            if section["start"] is None:
                section["start"] = seg["start"]
            section["end"] = seg["end"]
            section["segments"].append(seg)
            section["text"].append(seg["text"])
            duration = section["end"] - section["start"]
            gap = seg["start"] - last_end if last_end is not None else 0
            last_end = seg["end"]

            should_close = False
            if duration >= maximum:
                should_close = True
            elif duration >= target and gap >= min_gap:
                should_close = True
            elif self.soft_min_duration and duration >= self.soft_min_duration:
                should_close = True

            if should_close:
                sections.append(section)
                section = self._new_section(len(sections))

        if section["segments"]:
            sections.append(section)

        for sec in sections:
            paragraph = " ".join(sec["text"]).strip()
            sec["paragraph"] = paragraph
            if self.enable_titles:
                sec["title"] = self._format_title(paragraph)
            sec["quotes"] = self._extract_quotes(paragraph)
            sec["duration"] = sec["end"] - sec["start"]
            del sec["text"]
            del sec["segments"]

        return {"sections": sections, "language": language}

    def _format_title(self, text: str) -> str:
        raw_title = self._title_from_text(text)
        if self.trim_titles:
            raw_title = textwrap.shorten(raw_title, width=80, placeholder="…")
        if self.title_case == "sentence" and raw_title:
            raw_title = raw_title[0].upper() + raw_title[1:]
        elif self.title_case == "title":
            raw_title = raw_title.title()
        return raw_title or "Section"

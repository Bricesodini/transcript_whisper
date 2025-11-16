from __future__ import annotations

from typing import Dict, List, Optional

from text_cleaning import clean_human_text

from .models import Paragraph


class MarkdownRenderer:
    def __init__(self, cfg: Dict, keep_speakers: bool, flag_token: Optional[str]):
        self.cfg = cfg or {}
        self.keep_speakers = keep_speakers
        self.flag_token = flag_token

    def render(
        self,
        base_name: str,
        paragraphs: List[Paragraph],
        structure: Optional[Dict] = None,
        quotes: Optional[List[Dict]] = None,
    ) -> str:
        title_prefix = self.cfg.get("title_prefix", "Transcription nettoyée")
        lines = [f"# {title_prefix} — {base_name}", ""]

        sections = (
            self._sections_from_structure(structure)
            if structure
            else self._sections_from_paragraphs(paragraphs)
        )
        for section in sections:
            lines.append(section["heading"])
            lines.append("")
            lines.append(section["body"])
            if section.get("quotes"):
                lines.append("")
                lines.append("### Citations clés")
                for quote in section["quotes"]:
                    lines.append(f'- « {quote.strip()} »')
            lines.append("")

        if self.cfg.get("include_citations", True):
            block = self._build_citation_block(quotes, sections)
            if block:
                lines.append(self.cfg.get("citations_heading", "### Citations clés"))
                lines.extend(block)

        return "\n".join(line.rstrip() for line in lines).strip() + "\n"

    def _sections_from_structure(self, structure: Dict) -> List[Dict]:
        sections: List[Dict] = []
        for idx, entry in enumerate(structure.get("sections", [])):
            title = entry.get("title") or f"Section {idx + 1}"
            heading = f"## {title}"
            timestamp = entry.get("start")
            if timestamp is not None:
                heading += f" ({self._format_timestamp(timestamp)})"
            paragraph = clean_human_text(entry.get("paragraph") or "")
            quotes = [clean_human_text(q, dedupe=False) for q in entry.get("quotes") or []]
            sections.append({"heading": heading, "body": paragraph.strip(), "quotes": quotes})
        return sections

    def _sections_from_paragraphs(self, paragraphs: List[Paragraph]) -> List[Dict]:
        per_section = max(1, int(self.cfg.get("fallback_paragraphs_per_section", 4)))
        prefix = self.cfg.get("fallback_section_prefix", "Partie")
        sections: List[Dict] = []
        for idx in range(0, len(paragraphs), per_section):
            chunk = paragraphs[idx : idx + per_section]
            heading = f"## {prefix} {len(sections) + 1}"
            body_parts = [
                paragraph.render(self.keep_speakers, flag_token=self.flag_token) for paragraph in chunk if paragraph.items
            ]
            body = "\n\n".join(body_parts).strip()
            sections.append({"heading": heading, "body": body, "quotes": []})
        return sections

    def _build_citation_block(self, quotes: Optional[List[Dict]], sections: List[Dict]) -> List[str]:
        max_items = int(self.cfg.get("citations_max", 4))
        collected: List[str] = []
        if quotes:
            for entry in quotes:
                if entry.get("text"):
                    collected.append(entry["text"].strip())
                if len(collected) >= max_items:
                    break
        if not collected:
            for section in sections:
                for quote in section.get("quotes") or []:
                    collected.append(quote.strip())
                    if len(collected) >= max_items:
                        break
                if len(collected) >= max_items:
                    break
        return [f"- « {quote} »" for quote in collected]

    def _format_timestamp(self, seconds: float) -> str:
        total = int(seconds or 0)
        m, s = divmod(total, 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h:02}:{m:02}:{s:02}"
        return f"{m:02}:{s:02}"


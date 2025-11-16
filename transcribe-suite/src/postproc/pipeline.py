from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, Optional

from .config import load_postprocess_config
from .diagnostics import diagnose_bundle, locate_assets, read_clean_lines, read_jsonl, read_metrics
from .finalize import FinalComposer
from .low_confidence import LowConfidenceAnnotator
from .markdown import MarkdownRenderer
from .models import Paragraph
from .normalizer import EditorialNormalizer
from .qa import QAReporter


class PostProcessRunner:
    def __init__(self, config_path: Path, profile: Optional[str] = None, logger: Optional[logging.Logger] = None):
        config_bundle = load_postprocess_config(config_path, profile)
        self.general = config_bundle.get("general", {})
        self.profile = config_bundle.get("profile", {})
        self.logger = logger or logging.getLogger(__name__)

    def run(self, export_dir: Path, doc_id: Optional[str] = None) -> Dict[str, Path]:
        bundle = locate_assets(export_dir, doc_id)
        metrics = read_metrics(bundle)
        lines = read_clean_lines(bundle)
        diagnostics = diagnose_bundle(bundle, lines, metrics)

        normalizer = EditorialNormalizer(self.profile.get("normalize", {}))
        phrases, norm_stats = normalizer.run(lines)
        normalized_path = bundle.export_dir / f"{bundle.base_name}{self.general.get('normalized_suffix', '.clean.normalized.txt')}"
        self._write_normalized(normalized_path, phrases)

        low_conf_entries = read_jsonl(bundle.low_conf_jsonl)
        low_conf_annotator = LowConfidenceAnnotator(self.profile.get("low_confidence", {}))
        low_conf_stats = low_conf_annotator.apply(phrases, low_conf_entries)

        flag_token = (self.profile.get("low_confidence") or {}).get("flag_token")
        composer = FinalComposer(self.profile.get("paragraphs", {}), flag_token=flag_token)
        paragraphs = composer.build_paragraphs(phrases)
        final_text = composer.render_text(paragraphs)
        orphan_entries = low_conf_stats.get("orphan_entries") or []
        if orphan_entries:
            annex = self._render_orphan_block(orphan_entries, flag_token)
            if annex:
                final_text = f"{final_text}\n\n{annex}".strip()
        final_path = bundle.export_dir / f"{bundle.base_name}{self.general.get('final_suffix', '.clean.final.txt')}"
        self._write_text(final_path, final_text)

        structure = None
        if bundle.chapters_json and bundle.chapters_json.exists():
            structure = json.loads(bundle.chapters_json.read_text(encoding="utf-8"))
        quotes = read_jsonl(bundle.quotes_jsonl)
        markdown_renderer = MarkdownRenderer(self.profile.get("markdown", {}), composer.keep_speakers, flag_token=flag_token)
        markdown_text = markdown_renderer.render(bundle.base_name, paragraphs, structure=structure, quotes=quotes)
        markdown_path = bundle.export_dir / f"{bundle.base_name}{self.general.get('markdown_suffix', '.final.md')}"
        self._write_text(markdown_path, markdown_text, ensure_newline=False)

        qa = QAReporter(self.profile.get("qa", {}))
        qa_result = qa.run(phrases, norm_stats, low_conf_stats, final_text, diagnostics=diagnostics)
        qa_path = bundle.export_dir / f"{bundle.base_name}{self.general.get('qa_suffix', '.qa.json')}"
        qa_payload = qa_result.to_dict()
        qa_path.write_text(json.dumps(qa_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        return {
            "normalized": normalized_path,
            "final": final_path,
            "markdown": markdown_path,
            "qa": qa_path,
        }

    def _write_normalized(self, path: Path, phrases) -> None:
        lines = [phrase.as_line(keep_speaker=True) for phrase in phrases]
        text = "\n".join(lines).rstrip() + "\n"
        path.write_text(text, encoding="utf-8")

    def _write_text(self, path: Path, text: str, ensure_newline: bool = True) -> None:
        payload = text or ""
        if ensure_newline and payload and not payload.endswith("\n"):
            payload += "\n"
        if ensure_newline and not payload:
            payload = "\n"
        path.write_text(payload, encoding="utf-8")

    def _render_orphan_block(self, entries, flag_token: Optional[str]) -> str:
        if not entries:
            return ""
        heading = (self.profile.get("low_confidence") or {}).get("orphan_heading") or "Phrases à vérifier"
        lines = [f"{flag_token or '⚠️'} {heading}", ""]
        for entry in entries:
            text = (entry.get("text_machine") or entry.get("text") or "").strip()
            if text:
                lines.append(f"- {text}")
        return "\n".join(lines).strip()

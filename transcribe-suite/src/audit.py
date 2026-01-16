import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Optional


class AuditReporter:
    def __init__(self, cfg: Dict[str, Any], logger):
        self.cfg = cfg or {}
        self.logger = logger
        self.enabled = bool(self.cfg.get("enabled", True))
        self.max_examples = max(1, int(self.cfg.get("max_examples", 8)))

    def render(
        self,
        media_name: str,
        language: str,
        clean_report: Optional[Dict[str, Any]],
        polish_report: Optional[Dict[str, Any]],
        structure: Optional[Dict[str, Any]],
        chunks: Optional[List[Dict[str, Any]]],
        metrics: Optional[Dict[str, Any]] = None,
        low_conf_entries: Optional[List[Dict[str, Any]]] = None,
        low_conf_path: Optional[Path] = None,
        glossary_conflicts: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        timestamp = dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        lines: List[str] = [f"# Audit post-traitement — {media_name}", f"_Généré le {timestamp} (langue: {language})_"]

        if clean_report:
            lines.extend(self._render_clean_report(clean_report))
        if polish_report:
            lines.extend(self._render_polish_report(polish_report))
        if structure:
            lines.extend(self._render_structure_report(structure, chunks))
        if metrics:
            lines.extend(self._render_metrics(metrics))
        if low_conf_entries is not None:
            lines.extend(self._render_low_conf(low_conf_entries, low_conf_path))
        if clean_report:
            lines.extend(self._render_review_section(clean_report))
            glossary = clean_report.get("glossary") or []
            if glossary:
                lines.append("## Glossaire dynamique")
                for entry in glossary[: self.max_examples]:
                    lines.append(f"- {entry}")
        if glossary_conflicts:
            lines.append("## Conflits glossary (mode strict)")
            for conflict in glossary_conflicts[: self.max_examples]:
                lines.append(f"- `{conflict.get('word')}` vs `{conflict.get('preferred')}` → correction ignorée")
        return "\n".join(lines).strip() + "\n"

    def _render_clean_report(self, report: Dict[str, Any]) -> List[str]:
        lines = ["## Nettoyage (Cleaner)"]
        lines.append(
            f"- Segments: {report.get('input_segments', 0)} ➜ {report.get('output_segments', 0)} "
            f"(fusions courtes: {report.get('short_merges', 0)})"
        )
        lines.append(
            f"- Fillers supprimés: {report.get('fillers_removed', 0)} · corrections: {report.get('auto_corrections', 0)} "
            f"· redondances filtrées: {report.get('redundant_segments', 0)} "
            f"(garde-fous: {report.get('redundancy_guarded', 0)})"
        )
        lines.append(
            f"- Segments supprimés: {report.get('dropped_segments', 0)} · low-conf: {report.get('low_confidence_segments', 0)}"
        )
        return lines

    def _render_polish_report(self, report: Dict[str, Any]) -> List[str]:
        lines = ["## Polish (lecture)"]
        lines.append(
            f"- Segments: {report.get('input_segments', 0)} ➜ {report.get('output_segments', report.get('input_segments', 0))}"
        )
        lines.append(
            f"- Jointures courtes: {report.get('joined_segments', 0)} · marqueurs oraux supprimés: {report.get('oral_markers_removed', 0)} "
            f"· découpes phrases: {report.get('sentence_splits', 0)}"
        )
        return lines

    def _render_structure_report(self, structure: Dict[str, Any], chunks: Optional[List[Dict[str, Any]]]) -> List[str]:
        sections = structure.get("sections", [])
        lines = ["## Structuration & chunking"]
        lines.append(f"- Sections: {len(sections)} · langue détectée: {structure.get('language')}")
        if sections:
            avg_sentences = sum(sec.get("metadata", {}).get("sentence_count", 0) for sec in sections) / len(sections)
            lines.append(f"- Phrases moy./section: {avg_sentences:.1f}")
        if chunks:
            token_counts = [chunk.get("token_count", 0) for chunk in chunks]
            if token_counts:
                avg_tokens = sum(token_counts) / len(token_counts)
                max_tokens = max(token_counts)
                lines.append(f"- Chunks: {len(chunks)} · tokens moyen: {avg_tokens:.0f} · max: {max_tokens}")
        return lines

    def _render_metrics(self, metrics: Dict[str, Any]) -> List[str]:
        lines = ["## Métriques"]
        rows = {
            "Tokens total": metrics.get("tokens_total"),
            "Phrases total": metrics.get("phrases_total"),
            "Chunks total": metrics.get("chunks_total"),
            "Chunks confidence moyenne": metrics.get("chunk_confidence_mean"),
            "Low-conf spans": metrics.get("low_conf_count"),
        }
        lines.append("| Indicateur | Valeur |")
        lines.append("| --- | --- |")
        for key, value in rows.items():
            lines.append(f"| {key} | {value if value is not None else '—'} |")
        sparkline = metrics.get("sparkline")
        if sparkline:
            lines.append("")
            lines.append(f"Confiance globale : `{sparkline}`")
        return lines

    def _render_low_conf(self, entries: List[Dict[str, Any]], low_conf_path: Optional[Path]) -> List[str]:
        lines: List[str] = []
        if not entries and not low_conf_path:
            return lines
        lines.append("## File low-confidence")
        if low_conf_path:
            lines.append(f"- JSONL ➜ `{low_conf_path.name}`")
        for entry in entries[: self.max_examples]:
            start = entry.get("ts_start")
            end = entry.get("ts_end")
            reason = entry.get("reason")
            text = (entry.get("text_human") or entry.get("text_machine") or "")[:120]
            lines.append(f"- `{entry.get('id')}` t={start:.2f}s→{end:.2f}s ({reason}) · {text}")
        return lines

    def _render_review_section(self, report: Dict[str, Any]) -> List[str]:
        examples = report.get("examples", {}).get("low_confidence") or []
        redundant = report.get("examples", {}).get("redundant") or []
        dropped = report.get("examples", {}).get("dropped") or []
        lines: List[str] = []
        if not (examples or redundant or dropped):
            return lines
        lines.append("## Zones à relire")
        if examples:
            lines.append("### Segments à faible confiance")
            for item in examples[: self.max_examples]:
                lines.append(f"- t={item.get('start', '?')}s · score={item.get('score', '?')}")
        if redundant:
            lines.append("### Segments redondants filtrés")
            for item in redundant[: self.max_examples]:
                preview = (item.get("text") or "")[:80]
                lines.append(f"- t={item.get('start', '?')}s · \"{preview}\"")
        if dropped:
            lines.append("### Segments supprimés")
            for item in dropped[: self.max_examples]:
                lines.append(f"- t={item.get('start', '?')}s · raison={item.get('reason', 'n/a')}")
        return lines

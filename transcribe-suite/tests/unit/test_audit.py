from pathlib import Path

from audit import AuditReporter


def test_audit_reporter_renders_markdown():
    reporter = AuditReporter({"enabled": True, "max_examples": 2}, logger=_DummyLogger())
    clean_report = {
        "input_segments": 10,
        "output_segments": 8,
        "short_merges": 2,
        "fillers_removed": 5,
        "auto_corrections": 3,
        "redundant_segments": 1,
        "dropped_segments": 1,
        "low_confidence_segments": 1,
        "examples": {
            "low_confidence": [{"start": 12.3, "score": 0.45}],
            "redundant": [{"start": 2.0, "text": "exemple"}],
            "dropped": [{"start": 3.0, "reason": "low_confidence"}],
        },
        "glossary": ["ChatGPT"],
    }
    polish_report = {"input_segments": 8, "output_segments": 7, "joined_segments": 1, "oral_markers_removed": 2, "sentence_splits": 1}
    structure = {"language": "fr", "sections": [{"metadata": {"sentence_count": 3}}]}
    chunks = [{"token_count": 200}, {"token_count": 250}]
    metrics = {"tokens_total": 450, "phrases_total": 9, "chunks_total": 2, "sparkline": "██░░"}
    low_conf = [{"id": "lc1", "ts_start": 1.0, "ts_end": 2.0, "reason": "low_mean", "text_human": "extrait"}]
    markdown = reporter.render(
        "test-media",
        "fr",
        clean_report,
        polish_report,
        structure,
        chunks,
        metrics=metrics,
        low_conf_entries=low_conf,
        low_conf_path=Path("test.low_confidence.jsonl"),
        glossary_conflicts=[{"word": "Test", "preferred": "TEST"}],
    )
    assert "# Audit post-traitement" in markdown
    assert "Segments: 10" in markdown
    assert "Chunks" in markdown
    assert "Glossaire dynamique" in markdown
    assert "Métriques" in markdown
    assert "File low-confidence" in markdown
    assert "Conflits glossary" in markdown


class _DummyLogger:
    def info(self, *_, **__):
        return

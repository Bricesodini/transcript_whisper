import json
from pathlib import Path

from postproc.finalize import FinalComposer
from postproc.low_confidence import LowConfidenceAnnotator
from postproc.markdown import MarkdownRenderer
from postproc.models import Phrase
from postproc.normalizer import EditorialNormalizer
from postproc.pipeline import PostProcessRunner


def test_editorial_normalizer_removes_markers():
    cfg = {
        "remove_timestamps": True,
        "strip_bracketed_markers": True,
        "glossary": {"replacements": [{"source": "chat gpt", "target": "ChatGPT"}]},
    }
    normalizer = EditorialNormalizer(cfg)
    lines = ["SPEAKER_00: [00:00:01] chat gpt arrive (test)"]
    phrases, stats = normalizer.run(lines)
    assert stats["modified_lines"] == 1
    assert phrases[0].speaker == "SPEAKER_00"
    assert phrases[0].text == "ChatGPT arrive"


def test_low_confidence_matcher_flags_phrase():
    phrase = Phrase(index=0, speaker="S", raw_text="S: Bonjour monde", text="Bonjour monde")
    annotator = LowConfidenceAnnotator({"match_threshold": 0.4, "review_label": "À vérifier"})
    stats = annotator.apply(
        [phrase],
        [{"text_machine": "bonjour Monde", "score_mean": 0.2, "reason": "low_mean"}],
    )
    assert stats["matched"] == 1
    assert phrase.flags == ["À vérifier"]
    assert phrase.low_conf_score == 0.2


def test_markdown_renderer_fallback_sections():
    phrase = Phrase(index=0, speaker="S", raw_text="S: Salut", text="Salut")
    paragraph = FinalComposer({"max_sentences": 2, "keep_speakers": False}, flag_token=None).build_paragraphs([phrase])[0]
    renderer = MarkdownRenderer(
        {
            "title_prefix": "Doc",
            "fallback_section_prefix": "Bloc",
            "include_citations": False,
        },
        keep_speakers=False,
        flag_token=None,
    )
    md = renderer.render("Demo", [paragraph], structure=None, quotes=None)
    assert "# Doc — Demo" in md
    assert "## Bloc 1" in md
    assert "Salut" in md


def test_postprocess_runner_end_to_end(tmp_path: Path):
    export_dir = tmp_path / "exports"
    export_dir.mkdir()
    (export_dir / "demo.clean.txt").write_text(
        "SPEAKER_00: 00:01 Bonjour,  monde !\nSPEAKER_01: C'est un test.\n",
        encoding="utf-8",
    )
    metrics = {"phrases_total": 2, "low_conf_count": 1}
    (export_dir / "demo.metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (export_dir / "demo.low_confidence.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "id": "low1",
                "ts_start": 0.0,
                "ts_end": 1.0,
                "speaker": "SPEAKER_00",
                "text_machine": "Bonjour monde",
                "reason": "low_mean",
                "score_mean": 0.3,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    chapters = {
        "schema_version": "1.0.0",
        "language": "fr",
        "sections": [
            {
                "index": 0,
                "start": 0.0,
                "title": "Intro",
                "paragraph": "Bonjour monde. C'est un test.",
                "quotes": ["Bonjour monde."],
            }
        ],
    }
    (export_dir / "demo.chapters.json").write_text(json.dumps(chapters), encoding="utf-8")
    (export_dir / "demo.quotes.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "id": "q1",
                "section_id": "s1",
                "chunk_id": "c1",
                "ts_start": 0.0,
                "ts_end": 1.0,
                "text": "Bonjour monde.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[2]
    config = root / "configs" / "postprocess.default.yaml"
    runner = PostProcessRunner(config)
    outputs = runner.run(export_dir, doc_id="demo")

    normalized = outputs["normalized"].read_text(encoding="utf-8")
    assert "SPEAKER_00" in normalized
    final = outputs["final"].read_text(encoding="utf-8")
    assert "⚠️" in final
    markdown = outputs["markdown"].read_text(encoding="utf-8")
    assert "# Transcription nettoyée" in markdown
    qa = json.loads(outputs["qa"].read_text(encoding="utf-8"))
    assert qa["flagged_lines"] == 1

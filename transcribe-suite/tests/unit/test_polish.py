import json
from pathlib import Path

from polish import Polisher


def load_segments():
    fixture = Path(__file__).resolve().parent.parent / "fixtures" / "sample_segments.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def test_polish_merges_and_caps():
    cfg = {
        "enabled": True,
        "max_sentence_words": 18,
        "join_short_segments_ms": 1000,
        "normalize_ellipses": True,
        "normalize_quotes": True,
        "ensure_terminal_punct": True,
        "fr_nbsp_before": [":"],
        "fr_nbsp_after": ["«"],
        "replacements": [["chat gpt", "ChatGPT"]],
    }
    polisher = Polisher(cfg, logger=_DummyLogger())
    segments = polisher.run(load_segments(), lang="fr")
    assert len(segments) == 1
    assert "ChatGPT" in segments[0]["text"]
    assert segments[0]["text"].endswith(".")


def test_sentence_case_and_nbsp():
    cfg = {
        "enabled": True,
        "sentence_case": True,
        "acronym_whitelist": ["IA"],
        "enable_nbsp": True,
        "fr_nbsp_before": [":", ";", "!", "?", "»"],
        "fr_nbsp_after": ["«"],
        "ensure_terminal_punct": True,
    }
    polisher = Polisher(cfg, logger=_DummyLogger())
    segments = [
        {
            "start": 0.0,
            "end": 4.5,
            "text": 'remplacer Tous les emplois : « IA concrète ! » c\'est Ce Qui frappe',
            "speaker": "SPEAKER_00",
            "words": [],
        }
    ]
    polished = polisher.run(segments, lang="fr")
    text = polished[0]["text"]
    assert "Remplacer tous les emplois" in text
    assert "emplois\u00A0:" in text
    assert "«\u00A0IA" in text
    assert "concrète\u00A0!" in text
    assert "C'est ce qui frappe." in text
    assert "IA" in text


def test_lexicon_replacements():
    cfg = {
        "enabled": True,
        "lexicon": [
            {"pattern": r"\bchat\s*gpt\b", "replacement": "ChatGPT"},
            {"pattern": r"\bi[.\s]*a\b", "replacement": "IA"},
            {"pattern": r"\bpodcat(?P<plural>s)?\b", "replacement": r"podcast\g<plural>"},
            {"pattern": r"\bpar[\s-]+dessus\b", "replacement": "par-dessus"},
        ],
        "sentence_case": False,
        "ensure_terminal_punct": False,
    }
    polisher = Polisher(cfg, logger=_DummyLogger())
    segments = [
        {
            "start": 0.0,
            "end": 1.5,
            "text": "chat GPT parle d' iA et de podcats, parfois par dessus.",
            "speaker": "SPEAKER_00",
            "words": [],
        }
    ]
    polished = polisher.run(segments, lang="fr")
    text = polished[0]["text"]
    assert "ChatGPT" in text
    assert "IA" in text
    assert "podcast" in text
    assert "par-dessus" in text


def test_list_markers_and_quotes():
    cfg = {
        "enabled": True,
        "normalize_list_markers": True,
        "list_bullet_symbol": "•",
        "normalize_quotes": True,
        "enable_nbsp": True,
        "fr_nbsp_before": [":", ";", "!", "?", "»"],
        "fr_nbsp_after": ["«"],
        "sentence_case": False,
        "ensure_terminal_punct": False,
    }
    polisher = Polisher(cfg, logger=_DummyLogger())
    segments = [
        {
            "start": 0.0,
            "end": 5.0,
            "text": '- premier "item"\n- second ?',
            "speaker": "SPEAKER_00",
            "words": [],
        }
    ]
    polished = polisher.run(segments, lang="fr")
    text = polished[0]["text"]
    assert text.startswith("• premier")
    assert "• second" in text
    assert "«\u00A0item" in text
    assert "second\u00A0?" in text


class _DummyLogger:
    def info(self, *_, **__):
        return

    def warning(self, *_, **__):
        return

import json
from pathlib import Path

from glossary import GlossaryManager
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


def test_polish_removes_oral_markers():
    cfg = {
        "enabled": True,
        "strip_oral_markers": True,
        "oral_markers": ["tu vois"],
        "ensure_terminal_punct": True,
    }
    polisher = Polisher(cfg, logger=_DummyLogger())
    segments = [{"start": 0.0, "end": 1.0, "text": "tu vois on avance", "speaker": "S", "words": []}]
    polished = polisher.run(segments, lang="fr")
    assert polished[0]["text"].startswith("On avance")


def test_polish_capitalize_after_quotes():
    cfg = {
        "enabled": True,
        "strip_oral_markers": True,
        "oral_markers": ["tu vois"],
        "ensure_terminal_punct": True,
    }
    polisher = Polisher(cfg, logger=_DummyLogger())
    segments = [{"start": 0.0, "end": 1.0, "text": "« tu vois on avance »", "speaker": "S", "words": []}]
    polished = polisher.run(segments, lang="fr")
    assert "« On avance »." in polished[0]["text"] or "« On avance »" in polished[0]["text"]


def test_polish_keeps_numeric_leading_tokens():
    cfg = {
        "enabled": True,
        "strip_oral_markers": True,
        "oral_markers": ["tu vois"],
        "sentence_case": True,
    }
    polisher = Polisher(cfg, logger=_DummyLogger())
    segments = [{"start": 0.0, "end": 1.0, "text": "tu vois 2025 est une grande année", "speaker": "S", "words": []}]
    polished = polisher.run(segments, lang="fr")
    assert polished[0]["text"].startswith("2025 est une grande année")


def test_polish_respects_glossary_case():
    cfg = {
        "enabled": True,
        "sentence_case": True,
        "ensure_terminal_punct": True,
    }
    glossary = GlossaryManager({"entries": ["OpenAI"]})
    polisher = Polisher(cfg, logger=_DummyLogger(), glossary=glossary)
    segments = [{"start": 0.0, "end": 1.0, "text": "openai lance un produit", "speaker": "S", "words": []}]
    polished = polisher.run(segments, lang="fr")
    assert "OpenAI" in polished[0]["text"]


def test_polish_soft_punctuation_respects_quotes():
    cfg = {
        "enabled": True,
        "sentence_case": True,
        "punctuation": {"soft_after": [","]},
        "ensure_terminal_punct": True,
    }
    polisher = Polisher(cfg, logger=_DummyLogger())
    segments = [
        {"start": 0.0, "end": 2.0, "text": 'il dit, "Mais alors ?" et continue', "speaker": "S0", "words": []}
    ]
    polished = polisher.run(segments, lang="fr")
    text = polished[0]["text"]
    assert "«\u00A0Mais alors ? »" in text or "« Mais alors ? »" in text


class _DummyLogger:
    def debug(self, *_, **__):
        return

    def info(self, *_, **__):
        return

    def warning(self, *_, **__):
        return

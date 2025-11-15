from structure import Structurer


def test_structure_disables_titles():
    config = {"structure": {"enable_titles": False}}
    structurer = Structurer(config, logger=_DummyLogger())
    segments = [
        {"start": 0.0, "end": 2.0, "text": "Bonjour tout le monde."},
        {"start": 3.0, "end": 5.0, "text": "On continue ici."},
    ]
    data = structurer.run(segments, language="fr", source_id="media", confidence_threshold=0.5)
    assert data["sections"]
    assert "title" not in data["sections"][0]


def test_structure_keeps_titles_when_enabled():
    config = {"structure": {"enable_titles": True}}
    structurer = Structurer(config, logger=_DummyLogger())
    segments = [
        {"start": 0.0, "end": 2.0, "text": "Bonjour tout le monde."},
        {"start": 3.0, "end": 5.0, "text": "On continue ici."},
    ]
    data = structurer.run(segments, language="fr", source_id="media", confidence_threshold=0.5)
    assert "title" in data["sections"][0]


def test_structure_emits_sentences_metadata():
    config = {"structure": {"enable_titles": False}}
    structurer = Structurer(config, logger=_DummyLogger())
    segments = [
        {"start": 0.0, "end": 4.0, "text": "Bonjour tout le monde. On démarre.", "speaker": "S0", "confidence": 0.8},
        {"start": 4.0, "end": 8.0, "text": "Deuxième phrase ici.", "speaker": "S1", "confidence": 0.7},
    ]
    data = structurer.run(segments, language="fr", source_id="media", confidence_threshold=0.5)
    section = data["sections"][0]
    assert section["sentences"]
    assert section["paragraphs"]
    assert section["metadata"]["sentence_count"] == len(section["sentences"])
    assert "S0" in section["metadata"]["speaker_histogram"]
    assert section.get("section_id")


class _DummyLogger:
    def info(self, *_, **__):
        return

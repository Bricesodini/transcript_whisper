from chunker import Chunker


def test_chunker_builds_chunks_with_overlap():
    cfg = {
        "enabled": True,
        "target_tokens": 20,
        "max_tokens": 40,
        "min_sentences": 1,
        "overlap_sentences": 1,
    }
    chunker = Chunker(cfg, logger=_DummyLogger())
    structure = {
        "sections": [
            {
                "index": 0,
                "section_id": "sec_0",
                "sentences": [
                    {"text": "Phrase un.", "text_machine": "Phrase un.", "start": 0.0, "end": 1.0, "speaker": "S0", "tokens": 2, "confidence_mean": 0.8, "confidence_p05": 0.7, "low_duration": 0.0},
                    {"text": "Phrase deux.", "text_machine": "Phrase deux.", "start": 1.0, "end": 2.0, "speaker": "S1", "tokens": 2, "confidence_mean": 0.7, "confidence_p05": 0.6, "low_duration": 0.1},
                    {"text": "Phrase trois.", "text_machine": "Phrase trois.", "start": 2.0, "end": 3.0, "speaker": "S0", "tokens": 2, "confidence_mean": 0.9, "confidence_p05": 0.8, "low_duration": 0.0},
                    {"text": "Phrase quatre.", "text_machine": "Phrase quatre.", "start": 3.0, "end": 4.0, "speaker": "S1", "tokens": 2, "confidence_mean": 0.65, "confidence_p05": 0.5, "low_duration": 0.2},
                ],
            }
        ]
    }
    chunks = chunker.run(structure, language="fr", document_id="doc")
    assert chunks
    first = chunks[0]
    assert first["text_human"]
    assert first["text_machine"]
    assert first["speakers"]["S0"] >= 1
    assert isinstance(first["sentences"][0]["tokens"], int)
    assert "speaker_majority" in first
    assert "section_ids" in first


class _DummyLogger:
    def info(self, *_, **__):
        return

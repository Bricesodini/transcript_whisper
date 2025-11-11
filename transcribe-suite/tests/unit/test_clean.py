from clean import Cleaner


def test_cleaner_removes_fillers_and_caps():
    config = {
        "languages": {"fr": {"fillers": ["euh"]}},
        "cleaning": {
            "remove_fillers": True,
            "capitalize_sentence_start": True,
            "min_segment_duration": 0.5,
            "max_segment_gap": 1.0,
        },
    }
    cleaner = Cleaner(config, logger=_DummyLogger())
    segments = [
        {"start": 0.0, "end": 0.4, "text": "euh bonjour", "words": []},
        {"start": 0.5, "end": 1.0, "text": "tout le monde", "words": []},
    ]
    cleaned = cleaner.run(segments, language="fr")
    assert cleaned[0]["text"].lower().startswith("bonjour")
    assert "euh" not in cleaned[0]["text"].lower()


def test_merge_short_segments_same_speaker():
    config = {
        "cleaning": {
            "merge_short_segments": {"enabled": True, "max_duration": 0.8, "max_gap": 0.3},
            "min_segment_duration": 0.2,
            "max_segment_gap": 0.5,
        }
    }
    cleaner = Cleaner(config, logger=_DummyLogger())
    segments = [
        {"start": 0.0, "end": 1.0, "text": "premier segment", "speaker": "SPEAKER_00", "words": []},
        {"start": 1.05, "end": 1.7, "text": "court", "speaker": "SPEAKER_00", "words": []},
    ]
    cleaned = cleaner.run(segments, language="fr")
    assert len(cleaned) == 1
    assert "premier segment court" in cleaned[0]["text"]


class _DummyLogger:
    def warning(self, *_, **__):
        return

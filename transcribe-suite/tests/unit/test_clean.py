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
    assert cleaned[0]["text_human"].lower().startswith("bonjour")
    assert "euh" not in cleaned[0]["text_human"].lower()


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
    assert "premier segment court" in cleaned[0]["text_human"]


def test_cleaner_normalizes_numbers_and_redundancy():
    config = {
        "numbers": {"human_numbers": True},
        "cleaning": {
            "redundancy": {"enabled": True, "similarity": 0.9, "window": 3},
        },
    }
    cleaner = Cleaner(config, logger=_DummyLogger())
    segments = [
        {"start": 0.0, "end": 1.0, "text": "j'ai vingt-cinq idées originales", "words": []},
        {"start": 1.2, "end": 2.4, "text": "j'ai vingt cinq idées originales", "words": []},
    ]
    cleaned = cleaner.run(segments, language="fr")
    assert len(cleaned) == 1
    assert "vingt" in cleaned[0]["text_human"]
    assert "25" in cleaned[0]["text_machine"]
    report = cleaner.report()
    assert report["redundant_segments"] == 1


def test_cleaner_drops_low_confidence_segments():
    config = {
        "cleaning": {
            "confidence": {"segment_threshold": 0.6, "drop_segments": True, "word_threshold": 0.5},
        }
    }
    cleaner = Cleaner(config, logger=_DummyLogger())
    segments = [
        {
            "start": 0.0,
            "end": 1.0,
            "text": "mot incertain",
            "words": [{"word": "mot", "probability": 0.4}],
        }
    ]
    cleaned = cleaner.run(segments, language="fr")
    assert cleaned == []
    report = cleaner.report()
    assert report["dropped_segments"] == 1


def test_cleaner_redundancy_guard_keeps_distant_segments():
    config = {
        "cleaning": {
            "redundancy": {"enabled": True, "similarity": 0.95, "max_gap": 1.0, "window": 4},
        }
    }
    cleaner = Cleaner(config, logger=_DummyLogger())
    segments = [
        {"start": 0.0, "end": 1.0, "text": "en résumé nous repartons", "words": []},
        {"start": 10.0, "end": 11.0, "text": "en résumé nous repartons", "words": []},
    ]
    cleaned = cleaner.run(segments, language="fr")
    assert len(cleaned) == 2
    report = cleaner.report()
    assert report["redundancy_guarded"] >= 1


class _DummyLogger:
    def warning(self, *_, **__):
        return

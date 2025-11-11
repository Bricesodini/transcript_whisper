from diarize import Diarizer


def test_single_speaker_segments_merge():
    diarizer = Diarizer({"diarization": {"merge_single_speaker": True}}, logger=_DummyLogger())
    segments = [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"},
        {"start": 1.1, "end": 2.0, "speaker": "SPEAKER_00"},
    ]
    merged = diarizer._stabilize_segments(segments)
    assert len(merged) == 1
    assert merged[0]["start"] == 0.0
    assert merged[0]["end"] == 2.0


def test_min_turn_blocks_short_speaker_switch():
    diarizer = Diarizer({"diarization": {"min_speaker_turn": 1.2}}, logger=_DummyLogger())
    segments = [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
        {"start": 2.0, "end": 2.4, "speaker": "SPEAKER_01"},
        {"start": 2.4, "end": 4.0, "speaker": "SPEAKER_00"},
    ]
    merged = diarizer._stabilize_segments(segments)
    assert len(merged) == 1
    assert merged[0]["speaker"] == "SPEAKER_00"
    assert merged[0]["end"] == 4.0


class _DummyLogger:
    def info(self, *_, **__):
        return

    def warning(self, *_, **__):
        return

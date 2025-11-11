from pathlib import Path

from refine import SegmentRefiner


def test_refiner_marks_segments_and_applies_patch(tmp_path):
    config = {
        "refine": {
            "enabled": True,
            "low_conf_threshold": 0.5,
            "min_low_conf_ratio": 0.1,
            "padding": 0.1,
        }
    }
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"\x00")
    asr = _DummyASR()

    class _TestableRefiner(SegmentRefiner):
        def _refine_segment(self, index, segment, audio_path, language, build_dir):
            updated = dict(segment)
            updated["text"] = f"refined-{index}"
            updated["words"] = segment.get("words", [])
            return updated

    refiner = _TestableRefiner(config, logger=_DummyLogger(), asr_processor=asr)
    segments = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "ok",
            "words": [
                {"word": "ok", "probability": 0.9},
            ],
        },
        {
            "start": 2.0,
            "end": 4.0,
            "text": "douteux",
            "words": [
                {"word": "un", "probability": 0.2},
                {"word": "mot", "probability": 0.8},
            ],
        },
    ]
    refined = refiner.run(audio_path, segments, "fr", tmp_path)
    assert refined[0]["text"] == "ok"
    assert refined[1]["text"] == "refined-1"


class _DummyLogger:
    def info(self, *_, **__):
        return

    def warning(self, *_, **__):
        return


class _DummyASR:
    asr_cfg = {}

    def load_model(self):
        raise RuntimeError("should not be called in test")

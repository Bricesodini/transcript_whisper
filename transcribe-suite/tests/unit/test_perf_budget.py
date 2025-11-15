import time

from clean import Cleaner


def test_cleaner_performance_budget():
    config = {
        "cleaning": {
            "remove_fillers": True,
            "min_segment_duration": 0.1,
            "max_segment_gap": 0.3,
        }
    }
    cleaner = Cleaner(config, logger=_DummyLogger())
    segments = [
        {"start": float(idx), "end": float(idx) + 0.8, "text": f"phrase numéro {idx}", "words": []}
        for idx in range(200)
    ]
    start = time.perf_counter()
    cleaner.run(segments, language="fr")
    duration = time.perf_counter() - start
    assert duration < 0.5


class _DummyLogger:
    def warning(self, *_, **__):
        return

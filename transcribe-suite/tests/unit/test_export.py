from pathlib import Path

from export import Exporter


def test_exporter_writes_utf8(tmp_path):
    exporter = Exporter({"export": {"subtitle_line_length": 72}}, logger=_DummyLogger())
    segments = [{"start": 0, "end": 1, "text": "Salut", "speaker": "A"}]
    structure = {"language": "fr", "sections": []}
    artifacts = exporter.run(
        base_name="test",
        out_dir=tmp_path,
        segments=segments,
        structure=structure,
        aligned_path=tmp_path / "aligned.json",
        formats=["txt"],
    )
    txt_path = artifacts["txt"]
    data = txt_path.read_bytes()
    assert data.startswith("A: Salut".encode("utf-8"))
    assert b"\r\n" not in data


def test_low_confidence_markup(tmp_path):
    cfg = {
        "export": {
            "low_confidence": {
                "threshold": 0.5,
                "formats": {
                    "txt": {"template": "**[{word}?]**"},
                    "md": {"template": "**[{word}?]**"},
                },
            }
        }
    }
    exporter = Exporter(cfg, logger=_DummyLogger())
    segments = [
        {
            "start": 0.0,
            "end": 2.0,
            "text": "Remplacer tous les emplois.",
            "speaker": "SPEAKER_00",
            "words": [
                {"word": "Remplacer", "probability": 0.9},
                {"word": "tous", "probability": 0.3},
                {"word": "les", "probability": 0.8},
                {"word": "emplois", "probability": 0.2},
            ],
        }
    ]
    structure = {
        "language": "fr",
        "sections": [
            {
                "start": 0.0,
                "end": 2.0,
                "paragraph": segments[0]["text"],
                "title": "Remplacer",
                "quotes": [],
            }
        ],
    }
    artifacts = exporter.run(
        base_name="low_conf",
        out_dir=tmp_path,
        segments=segments,
        structure=structure,
        aligned_path=tmp_path / "aligned.json",
        formats=["txt", "md"],
    )
    txt_content = artifacts["txt"].read_text(encoding="utf-8")
    md_content = artifacts["md"].read_text(encoding="utf-8")
    assert "**[tous?]**" in txt_content
    assert "**[emplois?]**" in txt_content
    assert "**[tous?]**" in md_content
    assert "**[emplois?]**" in md_content


def test_clean_txt_export(tmp_path):
    exporter = Exporter({"export": {}}, logger=_DummyLogger())
    segments = [
        {"start": 0.0, "end": 2.0, "text": "Bonjour tout le monde."},
        {"start": 2.1, "end": 4.0, "text": "On continue avec un exemple simple."},
    ]
    structure = {
        "language": "fr",
        "sections": [
            {
                "start": 0.0,
                "end": 4.0,
                "paragraph": " ".join(seg["text"] for seg in segments),
                "title": "Introduction",
                "quotes": ["Bonjour tout le monde."],
            }
        ],
    }
    artifacts = exporter.run(
        base_name="clean",
        out_dir=tmp_path,
        segments=segments,
        structure=structure,
        aligned_path=tmp_path / "aligned.json",
        formats=["clean_txt"],
    )
    content = artifacts["clean_txt"].read_text(encoding="utf-8")
    assert "Citations clés" not in content
    assert "Introduction" in content
    assert "Bonjour tout le monde." in content


class _DummyLogger:
    def debug(self, *_, **__):
        return

    def info(self, *_, **__):
        return

    def warning(self, *_, **__):
        return

    def error(self, *_, **__):
        return

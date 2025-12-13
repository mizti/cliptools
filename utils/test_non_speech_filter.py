from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from utils.json_to_srt_sentences import generate_srt_for_speaker
from utils.srt_parser import parse_srt_blocks


def _make_azureish_json(path: Path) -> None:
    data = [
        {
            "speaker": 1,
            "offset": "PT0S",
            "duration": "PT1S",
            "nBest": [
                {
                    "display": "*music*",
                    "words": [
                        {"word": "*music*", "offset": "PT0S", "duration": "PT1S"}
                    ],
                }
            ],
        },
        {
            "speaker": 1,
            "offset": "PT1S",
            "duration": "PT1S",
            "nBest": [
                {
                    "display": "Hello world.",
                    "words": [
                        {"word": "Hello", "offset": "PT1S", "duration": "PT0.5S"},
                        {"word": "world.", "offset": "PT1.5S", "duration": "PT0.5S"},
                    ],
                }
            ],
        },
    ]
    path.write_text(json.dumps(data), encoding="utf-8")


def test_drop_music_enabled() -> None:
    with tempfile.TemporaryDirectory() as td:
        json_path = Path(td) / "azure-stt.json"
        _make_azureish_json(json_path)

        os.environ["CLIPTOOLS_DROP_NON_SPEECH"] = "1"
        try:
            srt_text = generate_srt_for_speaker(json_path, 1)
        finally:
            os.environ.pop("CLIPTOOLS_DROP_NON_SPEECH", None)

    blocks = parse_srt_blocks(srt_text)
    text = "\n".join("\n".join(b.lines) for b in blocks)
    assert "*music*" not in text
    assert "Hello world." in text


def test_drop_music_disabled() -> None:
    with tempfile.TemporaryDirectory() as td:
        json_path = Path(td) / "azure-stt.json"
        _make_azureish_json(json_path)
        srt_text = generate_srt_for_speaker(json_path, 1)

    blocks = parse_srt_blocks(srt_text)
    text = "\n".join("\n".join(b.lines) for b in blocks)
    assert "*music*" in text


if __name__ == "__main__":
    test_drop_music_enabled()
    test_drop_music_disabled()
    print("ok")

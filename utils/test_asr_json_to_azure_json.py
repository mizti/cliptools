from utils.asr_json_to_azure_json import convert


def test_convert_preserves_segment_and_word_timestamps():
    result = convert(
        {
            "segments": [
                {"start": 1.0, "end": 2.0, "text": "Hello world."},
            ],
            "word_segments": [
                {"start": 1.0, "end": 1.4, "word": "Hello"},
                {"start": 1.5, "end": 2.0, "word": "world."},
            ],
        }
    )

    assert result == [
        {
            "speaker": 1,
            "offset": "PT1S",
            "duration": "PT1S",
            "nBest": [
                {
                    "display": "Hello world.",
                    "words": [
                        {"word": "Hello", "offset": "PT1S", "duration": "PT0.4S"},
                        {"word": "world.", "offset": "PT1.5S", "duration": "PT0.5S"},
                    ],
                }
            ],
        }
    ]


def test_convert_rejects_invalid_timestamps():
    result = convert(
        {
            "segments": [
                {"start": float("nan"), "end": 1.0, "text": "invalid"},
                {"start": 2.0, "end": 1.0, "text": "backward"},
            ],
            "word_segments": [
                {"start": 0.0, "end": 0.0, "word": "zero"},
                {"start": 0.0, "end": float("inf"), "word": "infinite"},
            ],
        }
    )

    assert result == []


def test_convert_uses_segment_word_ownership_for_overlapping_segments():
    result = convert(
        {
            "segments": [
                {
                    "start": 0.0,
                    "end": 2.0,
                    "text": "first",
                    "words": [{"start": 0.5, "end": 1.8, "word": "first"}],
                },
                {
                    "start": 1.5,
                    "end": 3.0,
                    "text": "second",
                    "words": [{"start": 1.6, "end": 2.5, "word": "second"}],
                },
            ],
            "word_segments": [
                {"start": 0.5, "end": 1.8, "word": "first"},
                {"start": 1.6, "end": 2.5, "word": "second"},
            ],
        }
    )

    assert [
        word["word"]
        for phrase in result
        for word in phrase["nBest"][0]["words"]
    ] == ["first", "second"]
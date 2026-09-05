from utils.whisperkit_cli import build_common_result


def test_build_common_result_preserves_timing_and_scores():
    report = {
        "language": "en",
        "segments": [
            {
                "start": 1.0,
                "end": 2.4,
                "text": "stale display text",
                "words": [
                    {
                        "word": " Hello",
                        "tokens": [2425],
                        "start": 1.0,
                        "end": 1.4,
                        "probability": 0.9,
                    },
                    {
                        "word": " world.",
                        "tokens": [1002, 13],
                        "start": 1.4,
                        "end": 2.4,
                        "probability": 0.8,
                    },
                ],
            }
        ],
    }

    result = build_common_result(report)

    assert result["language"] == "en"
    assert result["segments"] == [
        {
            "start": 1.0,
            "end": 2.4,
            "text": "Hello world.",
            "words": [
                {"word": "Hello", "start": 1.0, "end": 1.4, "score": 0.9},
                {"word": "world.", "start": 1.4, "end": 2.4, "score": 0.8},
            ],
        }
    ]
    assert result["word_segments"] == result["segments"][0]["words"]


def test_build_common_result_drops_invalid_words_and_segments():
    report = {
        "language": "en",
        "segments": [
            {"start": float("nan"), "end": 1, "text": "nan", "words": []},
            {
                "start": 0,
                "end": 1,
                "text": "valid",
                "words": [
                    {"word": "", "start": 0, "end": 0.2},
                    {"word": "backward", "start": 0.8, "end": 0.4},
                ],
            },
            {"start": 3, "end": 2, "text": "invalid", "words": []},
        ],
    }

    result = build_common_result(report)

    assert result["segments"] == []
    assert result["word_segments"] == []


def test_build_common_result_sorts_incremental_segments_before_flattening():
    report = {
        "language": "en",
        "segments": [
            {
                "start": 5,
                "end": 6,
                "text": "later",
                "words": [{"word": "later", "start": 5, "end": 6}],
            },
            {
                "start": 1,
                "end": 2,
                "text": "earlier",
                "words": [{"word": "earlier", "start": 1, "end": 2}],
            },
        ],
    }

    result = build_common_result(report)

    assert [segment["start"] for segment in result["segments"]] == [1.0, 5.0]
    assert [word["word"] for word in result["word_segments"]] == ["earlier", "later"]


def test_build_common_result_drops_fully_contained_competing_segment():
    report = {
        "language": "en",
        "segments": [
            {
                "start": 1.5,
                "end": 2.0,
                "text": "competing",
                "words": [{"word": "competing", "start": 1.5, "end": 2.0}],
            },
            {
                "start": 1.0,
                "end": 3.0,
                "text": "complete hypothesis",
                "words": [
                    {"word": "complete", "start": 1.0, "end": 1.8},
                    {"word": "hypothesis", "start": 1.8, "end": 3.0},
                ],
            },
            {
                "start": 4.0,
                "end": 5.0,
                "text": "later",
                "words": [{"word": "later", "start": 4.0, "end": 5.0}],
            },
        ],
    }

    result = build_common_result(report)

    assert [segment["text"] for segment in result["segments"]] == [
        "complete hypothesis",
        "later",
    ]
    assert [word["word"] for word in result["word_segments"]] == [
        "complete",
        "hypothesis",
        "later",
    ]


def test_build_common_result_drops_padded_window_hallucinations():
    report = {
        "language": "en",
        "timings": {"inputAudioSeconds": 18.0},
        "segments": [
            {
                "start": 17.5,
                "end": 41.2,
                "text": " Last *music*",
                "words": [
                    {"word": " Last", "start": 17.5, "end": 18.1},
                    {"word": " *music*", "start": 40.38, "end": 41.2},
                ],
            }
        ],
    }

    result = build_common_result(report)

    assert result["segments"] == [
        {
            "start": 17.5,
            "end": 18.0,
            "text": "Last",
            "words": [{"word": "Last", "start": 17.5, "end": 18.0}],
        }
    ]
    assert result["word_segments"] == [
        {"word": "Last", "start": 17.5, "end": 18.0}
    ]
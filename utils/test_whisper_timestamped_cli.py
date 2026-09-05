import sys

from utils.whisper_timestamped_cli import build_common_result, load_model, parse_args


def test_parse_args_disables_previous_text_conditioning_by_default(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["whisper_timestamped_cli", "in.wav", "out.json"])

    args = parse_args()

    assert args.condition_on_previous_text is False


def test_load_model_keeps_sparse_alignment_heads_off_mps():
    alignment_heads = object()

    class FakeModel:
        def __init__(self):
            self.alignment_heads = alignment_heads
            self.device = "cpu"

        def to(self, device):
            assert not hasattr(self, "alignment_heads")
            self.device = device
            return self

    model = FakeModel()

    class FakeWhisper:
        @staticmethod
        def load_model(model_name, device):
            assert model_name == "turbo"
            assert device == "cpu"
            return model

    loaded = load_model(FakeWhisper, "turbo", "mps")

    assert loaded is model
    assert loaded.device == "mps"
    assert loaded.alignment_heads is alignment_heads


def test_build_common_result_normalizes_words_and_confidence():
    result = build_common_result(
        {
            "language": "en",
            "segments": [
                {
                    "start": 1.0,
                    "end": 2.5,
                    "text": "stale display text",
                    "words": [
                        {
                            "text": " Hello,",
                            "start": 1.0,
                            "end": 1.6,
                            "confidence": 0.91,
                        },
                        {
                            "text": " world!",
                            "start": 1.7,
                            "end": 2.5,
                            "confidence": 0.82,
                        },
                    ],
                }
            ],
        }
    )

    assert result["language"] == "en"
    assert result["segments"][0]["text"] == "Hello, world!"
    assert result["segments"][0]["words"][0] == {
        "word": "Hello,",
        "start": 1.0,
        "end": 1.6,
        "score": 0.91,
    }
    assert result["word_segments"] == result["segments"][0]["words"]
    assert result["word_segments"] is not result["segments"][0]["words"]


def test_build_common_result_drops_invalid_records():
    result = build_common_result(
        {
            "language": "en",
            "segments": [
                None,
                {"start": float("nan"), "end": 1, "text": "nan", "words": []},
                {
                    "start": 0,
                    "end": 1,
                    "text": "invalid words",
                    "words": [
                        {"text": "", "start": 0, "end": 0.2},
                        {"text": "backward", "start": 0.8, "end": 0.4},
                    ],
                },
                {"start": 3, "end": 2, "text": "invalid", "words": []},
            ],
        }
    )

    assert result["segments"] == []
    assert result["word_segments"] == []


def test_build_common_result_clips_audio_end_and_drops_padding_words():
    result = build_common_result(
        {
            "language": "en",
            "segments": [
                {
                    "start": 17.5,
                    "end": 41.2,
                    "text": "Last *music*",
                    "words": [
                        {"text": "Last", "start": 17.5, "end": 18.1},
                        {"text": "*music*", "start": 40.0, "end": 41.2},
                    ],
                }
            ],
        },
        audio_duration=18.0,
    )

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

from __future__ import annotations

import re

from utils.azure_spacy_segmenter import (
    _split_long_segment,
    align_segments_to_words,
)
from utils.azure_types import Word


def _tokens(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def test_long_segment_splits_only_between_words() -> None:
    text = (
        "He doesn't even say cool things while he's sitting on the chair."
    )

    parts = _split_long_segment(text, preferred_max_chars=20)

    assert len(parts) > 1
    assert [token for part in parts for token in _tokens(part)] == _tokens(text)
    assert all(not part.startswith("air") for part in parts)
    assert parts[-1] != "chair."


def test_single_overlong_word_is_not_cut() -> None:
    word = "supercalifragilisticexpialidocious"

    assert _split_long_segment(word, preferred_max_chars=10) == [word]


def test_non_positive_limit_disables_length_splitting() -> None:
    text = "This caption remains whole even though it is longer than the limit."

    assert _split_long_segment(text, preferred_max_chars=0) == [text]


def test_alignment_uses_text_word_counts_instead_of_character_ratios() -> None:
    display = "Supercalifragilisticexpialidocious tiny words follow here."
    words = [
        Word(text="Supercalifragilisticexpialidocious", start=1.0, end=1.5),
        Word(text="tiny", start=2.0, end=2.2),
        Word(text="words", start=2.3, end=2.5),
        Word(text="follow", start=2.6, end=2.8),
        Word(text="here.", start=2.9, end=3.1),
    ]

    segments = align_segments_to_words(
        words,
        [(display, 1.0, 3.1)],
        preferred_max_chars=35,
    )

    assert [segment.text for segment in segments] == [
        "Supercalifragilisticexpialidocious",
        "tiny words follow here.",
    ]
    assert [(segment.start, segment.end) for segment in segments] == [
        (1.0, 1.5),
        (2.0, 3.1),
    ]
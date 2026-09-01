from __future__ import annotations


from utils.srt_parser import (
    SRTBlock,
    extend_subtitle_tails,
    merge_short_adjacent_blocks,
    normalize_english_pronoun_i,
    renumber_blocks,
    validate_srt,
    blocks_to_text,
)


def _b(i: int, start: str, end: str, text: str) -> SRTBlock:
    return SRTBlock(index=i, start=start, end=end, lines=[text])


def test_normalize_english_pronoun_i_basic() -> None:
    blocks = [
        _b(1, "00:00:01,000", "00:00:02,000", "i am here"),
        _b(2, "00:00:02,000", "00:00:03,000", "i'm tired"),
        _b(3, "00:00:03,000", "00:00:04,000", "i've been there"),
    ]
    normalize_english_pronoun_i(blocks)
    assert blocks[0].text == "I am here"
    assert blocks[1].text == "I'm tired"
    assert blocks[2].text == "I've been there"


def test_merge_short_adjacent_blocks_merges_when_adjacent() -> None:
    blocks = [
        _b(1, "00:00:01,000", "00:00:02,000", "Thank"),
        _b(2, "00:00:02,000", "00:00:03,000", "you"),
        _b(3, "00:00:03,000", "00:00:04,000", "OK"),
    ]
    merged = merge_short_adjacent_blocks(blocks, max_len=3, exceptions={"OK", "Oh", "No", "Yes"})
    # "you" should merge into prev, "OK" should remain separate.
    assert len(merged) == 2
    assert merged[0].text == "Thank you"
    assert merged[0].end == "00:00:03,000"
    assert merged[1].text == "OK"


def test_renumber_blocks_contiguous_and_serializable() -> None:
    blocks = [
        _b(10, "00:00:01,000", "00:00:02,000", "Hello"),
        _b(20, "00:00:02,000", "00:00:03,000", "World"),
    ]
    out = renumber_blocks(blocks)
    assert [b.index for b in out] == [1, 2]

    text = blocks_to_text(out)
    ok, errors = validate_srt(text)
    assert ok, errors


def test_extend_subtitle_tails_adds_reading_time_without_overlap() -> None:
    blocks = [
        _b(1, "00:00:01,000", "00:00:02,000", "First"),
        _b(2, "00:00:02,500", "00:00:03,000", "Second"),
        _b(3, "00:00:05,000", "00:00:06,000", "Last"),
    ]

    extended = extend_subtitle_tails(blocks, hold_seconds=0.8)

    # The first cue is capped at the next cue instead of overlapping it.
    assert extended[0].end == "00:00:02,500"
    # A larger gap receives the full reading-time tail.
    assert extended[1].end == "00:00:03,800"
    # The final cue also remains visible after speech ends.
    assert extended[2].end == "00:00:06,800"

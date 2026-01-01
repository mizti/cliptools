from __future__ import annotations


from utils.srt_parser import (
    SRTBlock,
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

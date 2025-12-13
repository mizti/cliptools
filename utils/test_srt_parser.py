from __future__ import annotations

"""Small manual test harness for utils.srt_parser.

Run this file directly to see how SRT text is parsed into SRTBlock
objects and how it round-trips back to SRT text.

This is not a formal unit test framework on purpose; it's meant to be
quickly runnable from the terminal while iterating on the parser.
"""

from pathlib import Path

from utils.srt_parser import SRTBlock, blocks_to_text, parse_srt_blocks, validate_srt


def print_block(block: SRTBlock, idx: int) -> None:
    print(f"--- Block #{idx} ---")
    print(f"index: {block.index}")
    print(f"start: {block.start}")
    print(f"end:   {block.end}")
    print("text:")
    for line in block.lines:
        print(f"  {line}")
    print()


def test_sample_snippet() -> None:
    """Test parsing a small inline SRT snippet (no file I/O)."""

    sample = """34\n00:00:01,000 --> 00:00:04,000\nHello, world!\n\n35\n00:00:05,000 --> 00:00:07,000\nSecond line.\nWith two lines.\n\n\n"""

    print("[test] Inline sample snippet")
    blocks = parse_srt_blocks(sample)
    print(f"parsed blocks: {len(blocks)}")
    for i, b in enumerate(blocks, start=1):
        print_block(b, i)

    round_trip = blocks_to_text(blocks)
    print("[round-trip]")
    print(round_trip)

    ok, errors = validate_srt(sample)
    print(f"[validate] ok={ok}")
    for e in errors:
        print(f"  ! {e}")


def test_file(path: Path) -> None:
    """Parse an actual SRT file and show a short summary.

    Only the first few blocks are printed so this stays readable.
    """

    print(f"[test] File: {path}")
    text = path.read_text(encoding="utf-8")
    blocks = parse_srt_blocks(text)
    print(f"parsed blocks: {len(blocks)}")

    ok, errors = validate_srt(text)
    print(f"valid: {ok}")
    for e in errors:
        print(f"  ! {e}")

    for i, b in enumerate(blocks[:5], start=1):
        print_block(b, i)


if __name__ == "__main__":
    # 1) Inline snippet test
    test_sample_snippet()

    # 2) Optional: real file test if a path like clips/.../Speaker1_en-US.srt exists
    sample_paths = [
        Path("clips/ina-painter-outfit2/Speaker1_en-US.srt"),
        Path("clips/ina-painter-outfit2/Speaker1_en-US_test_fixed.srt"),
    ]

    for p in sample_paths:
        if p.exists():
            print("\n" + "=" * 40 + "\n")
            test_file(p)

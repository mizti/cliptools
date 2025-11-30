#!/usr/bin/env python
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path
from typing import List, Tuple

from .azure_spacy_segmenter import (
    PREFERRED_MAX_CHARS,
    PREFERRED_MIN_CHARS,
    align_segments_to_words,
)
from .azure_types import Word
from .srt_parser import SRTBlock, blocks_to_text, fill_short_gaps


def to_seconds(pt: str) -> float:
    """Convert Azure PTxxS style offset/duration to seconds (float)."""

    # Format examples: PT3M21.5S, PT7.4S, PT0.52S
    if not pt.startswith("PT"):
        raise ValueError(f"Unexpected duration format: {pt}")
    pt = pt[2:]
    hours = minutes = 0.0
    seconds = 0.0
    num = ""
    unit = ""
    for ch in pt:
        if ch.isdigit() or ch == ".":
            num += ch
        else:
            unit = ch
            if unit == "H":
                hours = float(num)
            elif unit == "M":
                minutes = float(num)
            elif unit == "S":
                seconds = float(num)
            num = ""
    return hours * 3600.0 + minutes * 60.0 + seconds


def format_ts(t: float) -> str:
    """Format seconds as HH:MM:SS,mmm for SRT."""

    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t - h * 3600) // 60)
    s = int(t - h * 3600 - m * 60)
    ms = int(round((t - math.floor(t)) * 1000))
    if ms == 1000:
        ms = 0
        s += 1
        if s == 60:
            s = 0
            m += 1
            if m == 60:
                m = 0
                h += 1
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def load_words_and_displays(path: Path, speaker: int) -> Tuple[List[Word], List[Tuple[str, float, float]]]:
    """Load all Word tokens and (display, start, end) tuples for a given speaker.

    Returns:
        words:  flattened list of Word
        phrases: list of (display_text, phrase_start, phrase_end)
    """

    data = json.loads(path.read_text(encoding="utf-8"))
    words: List[Word] = []
    phrases: List[Tuple[str, float, float]] = []

    for rec in data:
        if rec.get("speaker") != speaker:
            continue
        nbest = rec.get("nBest") or []
        if not nbest:
            continue
        cand = nbest[0]
        display = cand.get("display") or ""
        phrase_start = to_seconds(rec.get("offset"))
        phrase_end = phrase_start + to_seconds(rec.get("duration"))
        phrases.append((display, phrase_start, phrase_end))

        for w in cand.get("words") or []:
            try:
                w_start = to_seconds(w.get("offset"))
                w_end = w_start + to_seconds(w.get("duration"))
            except Exception:
                continue
            words.append(Word(text=w.get("word", ""), start=w_start, end=w_end))

    words.sort(key=lambda w: w.start)
    phrases.sort(key=lambda p: p[1])
    return words, phrases


def generate_srt_for_speaker(json_path: Path, speaker: int) -> str:
    words, phrases = load_words_and_displays(json_path, speaker)
    # Strategy: spaCy-based segmentation (see azure_spacy_segmenter).
    # For now we always use spaCy.
    # 別のルールベース分割に切り替えたくなったら、この関数内でパラメータを変えるか、
    # 別実装を呼び出す形で拡張する。
    min_chars = PREFERRED_MIN_CHARS
    preferred_max_chars = PREFERRED_MAX_CHARS

    segments_data = align_segments_to_words(
        words,
        phrases,
        min_chars=min_chars,
        preferred_max_chars=preferred_max_chars,
    )
    # Convert segments into SRTBlock list
    blocks: List[SRTBlock] = []
    for idx, seg in enumerate(segments_data, start=1):
        start, end, text = seg.start, seg.end, seg.text
        blocks.append(
            SRTBlock(
                index=idx,
                start=format_ts(start),
                end=format_ts(end),
                lines=[text],
            )
        )

    # Fill short gaps so subtitles do not disappear for <threshold gaps
    blocks = fill_short_gaps(blocks, threshold=0.8)

    return blocks_to_text(blocks)


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        print("Usage: json_to_srt_sentences.py <tmp_script.json> <speaker_id>", file=sys.stderr)
        return 1
    json_path = Path(argv[1])
    speaker = int(argv[2])
    srt = generate_srt_for_speaker(json_path, speaker)
    sys.stdout.write(srt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

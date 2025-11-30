#!/usr/bin/env python
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple


@dataclass
class Word:
    text: str
    start: float  # seconds
    end: float    # seconds


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


def split_display_into_sentences(display: str) -> List[str]:
    """Naive sentence splitter based on . ? ! in the display text.

    Keeps the punctuation at the end of each sentence.
    """

    sentences: List[str] = []
    buf: List[str] = []
    for ch in display:
        buf.append(ch)
        if ch in ".?!":
            sent = "".join(buf).strip()
            if sent:
                sentences.append(sent)
            buf = []
    # tail without terminal punctuation
    tail = "".join(buf).strip()
    if tail:
        sentences.append(tail)
    return sentences


def align_sentences_to_words(
    words: List[Word], phrases: List[Tuple[str, float, float]],
) -> List[Tuple[float, float, str]]:
    """Approximate mapping from display-based sentences to word time ranges.

    For now, we simply cut sentences by phrase, and within each phrase we
    approximate timing by taking the min/max of the words whose center lies
    inside the phrase interval.
    """

    if not words or not phrases:
        return []

    results: List[Tuple[float, float, str]] = []
    w_idx = 0

    for display, p_start, p_end in phrases:
        # collect words that fall into this phrase window (using midpoint)
        phrase_words: List[Word] = []
        while w_idx < len(words) and words[w_idx].start < p_end + 0.001:
            w = words[w_idx]
            mid = (w.start + w.end) / 2.0
            if p_start - 0.001 <= mid <= p_end + 0.001:
                phrase_words.append(w)
            w_idx += 1

        sents = split_display_into_sentences(display)
        if not sents:
            continue

        if not phrase_words:
            # No word-level info; fall back to whole phrase
            for s in sents:
                results.append((p_start, p_end, s))
            continue

        # Naive mapping: divide the phrase word list proportionally by
        # character length of each sentence.
        total_chars = sum(len(s) for s in sents)
        if total_chars == 0:
            continue

        word_pos = 0
        n_words = len(phrase_words)
        for i, sent in enumerate(sents):
            if not sent:
                continue
            if i == len(sents) - 1:
                # last sentence: take all remaining words
                seg_words = phrase_words[word_pos:]
            else:
                share = len(sent) / total_chars
                count = max(1, round(share * n_words))
                seg_words = phrase_words[word_pos : word_pos + count]
            if not seg_words:
                continue
            word_pos += len(seg_words)

            seg_start = seg_words[0].start
            seg_end = seg_words[-1].end
            results.append((seg_start, seg_end, sent))

    return results


def generate_srt_for_speaker(json_path: Path, speaker: int) -> str:
    words, phrases = load_words_and_displays(json_path, speaker)
    segments = align_sentences_to_words(words, phrases)
    lines: List[str] = []
    for idx, (start, end, text) in enumerate(segments, start=1):
        lines.append(str(idx))
        lines.append(f"{format_ts(start)} --> {format_ts(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


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

#!/usr/bin/env python
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

import spacy

from .azure_types import Word


# Load spaCy model once at import time. Adjust model name if needed.
_NLP = spacy.load("en_core_web_sm")


# Tunable heuristics (magic numbers) for segmentation behaviour.
# These are library defaults; tweak in this module when experimenting.

# Minimum preferred characters per segment after intra-phrase merging.
PREFERRED_MIN_CHARS: int = 6

# Soft upper bound for characters per segment. When a segment exceeds this
# length, we try to split it at a reasonable boundary (comma, conjunction,
# etc.) to keep subtitles readable.
PREFERRED_MAX_CHARS: int = 75


@dataclass
class Segment:
    start: float
    end: float
    text: str


def _merge_short_sentences(sents: List[str], min_chars: int) -> List[str]:
    """Simple post-process: merge very short sentences into their neighbour.

    This is a lightweight heuristic to avoid one-word segments like "Welcome".
    """

    if not sents:
        return sents

    merged: List[str] = []
    buf = sents[0]
    for s in sents[1:]:
        if len(buf) < min_chars:
            buf = f"{buf} {s}".strip()
        else:
            merged.append(buf)
            buf = s
    merged.append(buf)
    return merged


def _split_long_segment(text: str, preferred_max_chars: int) -> List[str]:
    """Split a long text chunk into smaller pieces near natural boundaries.

    This is a soft constraint: we try to keep each piece around
    ``preferred_max_chars`` without breaking too aggressively.
    """

    if len(text) <= preferred_max_chars:
        return [text]

    boundaries = [",", ";", ":", " but ", " and ", " so ", " or "]
    parts: List[str] = []
    remaining = text

    while len(remaining) > preferred_max_chars:
        # Look for the rightmost boundary before the limit.
        cut = -1
        window = remaining[: preferred_max_chars + 20]
        for b in boundaries:
            idx = window.rfind(b)
            if idx != -1:
                candidate = idx + len(b)
                if candidate <= preferred_max_chars + 5 and candidate > cut:
                    cut = candidate
        if cut == -1:
            # Fallback: hard cut at preferred_max_chars.
            cut = preferred_max_chars
        part = remaining[:cut].strip()
        if part:
            parts.append(part)
        remaining = remaining[cut:].lstrip()

    if remaining:
        parts.append(remaining)
    return parts


def split_text_with_spacy(
    text: str,
    min_chars: int = PREFERRED_MIN_CHARS,
    preferred_max_chars: int = PREFERRED_MAX_CHARS,
) -> List[str]:
    """Split text into sentences using spaCy, then post-process.

    Steps:
      * use spaCy to get base sentences
      * merge very short sentences to avoid tiny fragments
      * split any remaining overlong segments near natural boundaries
    """

    doc = _NLP(text)
    raw_sents: List[str] = [s.text.strip() for s in doc.sents if s.text.strip()]
    merged = _merge_short_sentences(raw_sents, min_chars=min_chars)

    final: List[str] = []
    for seg in merged:
        final.extend(_split_long_segment(seg, preferred_max_chars=preferred_max_chars))
    return final


def align_segments_to_words(
    words: List[Word],
    phrases: Iterable[Tuple[str, float, float]],
    *,
    min_chars: int = PREFERRED_MIN_CHARS,
    preferred_max_chars: int = PREFERRED_MAX_CHARS,
) -> List[Segment]:
    """Map spaCy-based segments back to word timings.

    Strategy:
      * For each Azure phrase window, collect words whose midpoint lies inside.
      * Use spaCy to split the phrase display into segments.
      * Distribute phrase words to segments proportionally by character length.
    """

    if not words:
        return []

    segments: List[Segment] = []
    w_idx = 0

    for display, p_start, p_end in phrases:
        # Collect words in this phrase window
        phrase_words: List[Word] = []
        while w_idx < len(words) and words[w_idx].start < p_end + 0.001:
            w = words[w_idx]
            mid = (w.start + w.end) / 2.0
            if p_start - 0.001 <= mid <= p_end + 0.001:
                phrase_words.append(w)
            w_idx += 1

        sents = split_text_with_spacy(
            display,
            min_chars=min_chars,
            preferred_max_chars=preferred_max_chars,
        )
        if not sents:
            continue

        if not phrase_words:
            # No word-level info; fall back to whole phrase timings
            for s in sents:
                segments.append(Segment(start=p_start, end=p_end, text=s))
            continue

        total_chars = sum(len(s) for s in sents)
        if total_chars == 0:
            continue

        word_pos = 0
        n_words = len(phrase_words)
        for i, sent in enumerate(sents):
            if not sent:
                continue
            if i == len(sents) - 1:
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
            segments.append(Segment(start=seg_start, end=seg_end, text=sent))

    return segments

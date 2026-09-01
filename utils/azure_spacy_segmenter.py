#!/usr/bin/env python
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Tuple

import spacy

from .azure_types import Word


# Load spaCy model once at import time. Adjust model name if needed.
_NLP = spacy.load("en_core_web_sm")


# Tunable heuristics (magic numbers) for segmentation behaviour.
# These are library defaults; tweak in this module when experimenting.

# Minimum preferred characters per segment after intra-phrase merging.
PREFERRED_MIN_CHARS: int = 4

# Soft upper bound for characters per segment. When a segment exceeds this
# length, we try to split it at a reasonable boundary (comma, conjunction,
# etc.) to keep subtitles readable.
PREFERRED_MAX_CHARS: int = 60


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
    ``preferred_max_chars``. A split is only made at whitespace, so a word is
    never cut in the middle. A single overlong token is kept intact.
    """

    if preferred_max_chars <= 0 or len(text) <= preferred_max_chars:
        return [text]

    parts: List[str] = []
    remaining = text.strip()

    while len(remaining) > preferred_max_chars:
        whitespace_cuts = [match.start() for match in re.finditer(r"\s+", remaining)]
        if not whitespace_cuts:
            # There is no legal split point. Keeping an overlong word is much
            # better than producing broken captions such as "ch" / "air".
            break

        # Prefer punctuation or a conjunction near the target length.
        soft_limit = preferred_max_chars + 5
        minimum_natural_cut = max(1, preferred_max_chars // 2)
        natural_cuts: List[int] = []
        for candidate in whitespace_cuts:
            if not minimum_natural_cut <= candidate <= soft_limit:
                continue
            left = remaining[:candidate].rstrip()
            right = remaining[candidate:].lstrip().lower()
            if left.endswith((",", ";", ":")) or re.match(
                r"^(?:but|and|so|or)\b", right
            ):
                natural_cuts.append(candidate)

        if natural_cuts:
            cut = natural_cuts[-1]
        else:
            before_limit = [cut for cut in whitespace_cuts if cut <= preferred_max_chars]
            if before_limit:
                cut = before_limit[-1]
            else:
                # The first token is longer than the preferred limit. Split
                # after that token rather than cutting the token itself.
                cut = whitespace_cuts[0]

        # Avoid leaving a single short word as the final caption merely to
        # satisfy the soft length target. Rebalance this last pair around the
        # middle when both resulting pieces can remain reasonably sized.
        tail_length = len(remaining[cut:].lstrip())
        minimum_tail = min(20, max(8, preferred_max_chars // 4))
        if tail_length < minimum_tail and len(remaining) <= preferred_max_chars * 2:
            balanced_cuts = [
                candidate
                for candidate in whitespace_cuts
                if minimum_tail <= candidate <= len(remaining) - minimum_tail
            ]
            if balanced_cuts:
                cut = min(
                    balanced_cuts,
                    key=lambda candidate: abs(candidate - len(remaining) / 2),
                )

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
            * Map each text segment to the same number of aligned words.
            * Fall back to cumulative proportions only when token counts differ.
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

        segment_word_counts = [len(re.findall(r"\S+", sent)) for sent in sents]
        total_text_words = sum(segment_word_counts)
        if total_text_words == 0:
            continue

        word_pos = 0
        n_words = len(phrase_words)
        for i, sent in enumerate(sents):
            if not sent:
                continue
            if i == len(sents) - 1:
                seg_words = phrase_words[word_pos:]
            else:
                if total_text_words == n_words:
                    # The display text and aligned words normally contain the
                    # same whitespace-delimited tokens. Since long captions
                    # are also split only at whitespace, this maps each text
                    # fragment to the exact corresponding word timestamps.
                    word_end = word_pos + segment_word_counts[i]
                else:
                    # Azure display text can occasionally tokenize differently
                    # from its word list. Use cumulative proportions as a
                    # defensive fallback while reserving one word for each
                    # remaining text fragment where possible.
                    consumed_text_words = sum(segment_word_counts[: i + 1])
                    word_end = round(consumed_text_words / total_text_words * n_words)
                    word_end = max(word_pos + 1, word_end)
                    remaining_segments = len(sents) - i - 1
                    if n_words >= len(sents):
                        word_end = min(word_end, n_words - remaining_segments)
                    word_end = min(word_end, n_words)
                seg_words = phrase_words[word_pos:word_end]
            if not seg_words:
                continue
            word_pos += len(seg_words)

            seg_start = seg_words[0].start
            seg_end = seg_words[-1].end
            segments.append(Segment(start=seg_start, end=seg_end, text=sent))

    return segments

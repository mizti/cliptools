from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

import re


@dataclass
class SRTBlock:
    """Represents a single SRT block.

    This is intentionally conservative and does not try to interpret
    the text content; it just stores the raw lines and parsed timing.
    """

    index: Optional[int]
    start: str
    end: str
    lines: List[str]

    @property
    def text(self) -> str:
        """Return the payload text of this block as a single string.

        This joins the lines with newlines and does not include the
        index or timestamp line.
        """

        return "\n".join(self.lines)

    def merge_with_next(self, nxt: "SRTBlock", *, sep: str = " ") -> None:
        """Merge the *next* block into this block.

        - Text is concatenated (single-line friendly by default).
        - Timing is extended to cover the next block.
        - Index is intentionally left unchanged; callers can renumber.

        This mutates ``self`` in place.
        """

        # Merge text payload
        left = self.text.strip()
        right = nxt.text.strip()
        merged = (left + (sep if (left and right) else "") + right).strip()
        self.lines = [merged] if merged else []

        # Extend time
        self.end = nxt.end


def _parse_timestamp_line(line: str) -> Optional[tuple[str, str]]:
    """Parse a timestamp line of the form

        00:00:01,000 --> 00:00:04,000

    Returns (start, end) as raw strings if it looks valid, otherwise None.
    This does *not* validate the actual time values beyond the basic shape.
    """

    if "-->" not in line:
        return None
    parts = line.split("-->")
    if len(parts) != 2:
        return None
    start = parts[0].strip()
    end = parts[1].strip()
    if not start or not end:
        return None
    return start, end


def parse_srt_blocks(text: str) -> List[SRTBlock]:
    """Parse an SRT string into a list of SRTBlock.

    This parser is tolerant of:
    - Leading/trailing blank lines
    - Blocks that start from an arbitrary index (e.g. 34, 35, ...)
    - Blocks without an explicit numeric index (index will be None)

    It expects the canonical SRT structure per block:

        <index?>
        <timestamp line>
        <one or more text lines>
        <blank line separator>

    If the first non-empty line of a block is not an integer, it is
    treated as part of the text and "no index" is assumed. The first
    subsequent line that looks like a timestamp line is used as timing.
    """

    lines = text.splitlines()
    blocks: List[SRTBlock] = []

    i = 0
    n = len(lines)
    while i < n:
        # Skip leading blank lines
        while i < n and not lines[i].strip():
            i += 1
        if i >= n:
            break

        idx: Optional[int] = None
        start: str = ""
        end: str = ""
        payload: List[str] = []

        # First non-empty line of the block: may be index or timestamp or text
        first_line = lines[i].strip()
        ts = _parse_timestamp_line(first_line)
        if ts is not None:
            # Block has no explicit index, this line is timestamp
            start, end = ts
            i += 1
        else:
            # Try to parse as index
            try:
                idx = int(first_line)
                i += 1
            except ValueError:
                # No index, treat as text until we see a timestamp
                idx = None
                payload.append(first_line)
                i += 1

        # If we haven't got a timestamp yet, expect it on the next
        # non-empty line that parses as a timestamp.
        while i < n and (not start and not end):
            if not lines[i].strip():
                # Preserve blank lines that appear before timestamp as text
                payload.append("")
                i += 1
                continue
            ts = _parse_timestamp_line(lines[i].strip())
            if ts is None:
                # Still text before a timestamp; keep accumulating
                payload.append(lines[i])
                i += 1
                continue
            start, end = ts
            i += 1

        # Now collect payload text lines until a blank line or EOF
        while i < n and lines[i].strip():
            payload.append(lines[i])
            i += 1

        # Skip the blank separator line if present
        if i < n and not lines[i].strip():
            i += 1

        # Only accept blocks that at least have a timestamp
        if start and end:
            blocks.append(SRTBlock(index=idx, start=start, end=end, lines=payload))

    return blocks


def validate_srt(text: str) -> Tuple[bool, List[str]]:
    """Validate that the given text looks like a sane SRT (or SRT fragment).

    This is intentionally conservative and focuses on structural issues
    that are likely to break subtitle tools. It does *not* guarantee
    full spec compliance, but it will flag:

    - Blocks without a timestamp line
    - Timestamp lines that don't follow the basic HH:MM:SS,mmm format
    - Blocks where start time is lexicographically greater than end time
    - Non-monotonic indices within the parsed region (when indices exist)

    Returns (ok, errors). If ok is True, errors will be an empty list.
    """

    blocks = parse_srt_blocks(text)
    errors: List[str] = []

    if not blocks:
        errors.append("No valid SRT blocks found.")
        return False, errors

    # Basic timestamp shape: 00:00:01,000
    def _looks_like_time(t: str) -> bool:
        parts = t.split(":")
        if len(parts) != 3:
            return False
        hh, mm, rest = parts
        if "," not in rest:
            return False
        ss, ms = rest.split(",", 1)
        return all(p.isdigit() for p in (hh, mm, ss, ms))

    # Check each block's timestamps
    for i, b in enumerate(blocks, start=1):
        if not (_looks_like_time(b.start) and _looks_like_time(b.end)):
            errors.append(
                f"Block {i}: timestamp does not look like HH:MM:SS,mmm -> '"
                f"{b.start} --> {b.end}'"
            )
        # Lexicographic compare is safe here because we enforce zero-padded
        # fixed-width timestamps above.
        if b.start > b.end:
            errors.append(
                f"Block {i}: start time '{b.start}' is after end time '{b.end}'."
            )

    # Check index monotonicity where indices are present
    last_index: Optional[int] = None
    for i, b in enumerate(blocks, start=1):
        if b.index is None:
            continue
        if last_index is not None and b.index < last_index:
            errors.append(
                f"Block {i}: index {b.index} is smaller than previous index {last_index}."
            )
        last_index = b.index

    return (len(errors) == 0), errors


def blocks_to_text(blocks: Iterable[SRTBlock]) -> str:
    """Serialize a sequence of SRTBlock back into SRT text.

    Index and timestamps are emitted as stored; this is useful for
    round-tripping parsed blocks after editing only the text lines.
    """

    out_lines: List[str] = []
    first = True
    for b in blocks:
        if not first:
            out_lines.append("")  # blank separator
        first = False

        if b.index is not None:
            out_lines.append(str(b.index))
        out_lines.append(f"{b.start} --> {b.end}")
        out_lines.extend(b.lines)

    return "\n".join(out_lines) + ("\n" if out_lines else "")


def renumber_blocks(blocks: Iterable[SRTBlock], start_index: int = 1) -> List[SRTBlock]:
    """Return a list of blocks with contiguous indices.

    This is useful after merges/splits so that the serialized SRT is always
    sane (no missing or duplicate indices).
    """

    seq = list(blocks)
    idx = start_index
    for b in seq:
        b.index = idx
        idx += 1
    return seq


def merge_short_adjacent_blocks(
    blocks: Iterable[SRTBlock],
    *,
    max_len: int = 3,
    exceptions: Optional[set[str]] = None,
) -> List[SRTBlock]:
    """Merge blocks whose text is very short and whose start is adjacent.

    Rule:
      - If ``len(cur.text.strip()) <= max_len`` and
      - ``prev.end == cur.start`` and
      - ``cur.text.strip()`` is not in ``exceptions``
    then merge cur into prev.
    """

    exc = {e.strip() for e in (exceptions or set()) if e.strip()}
    seq: List[SRTBlock] = list(blocks)
    if len(seq) < 2:
        return seq

    out: List[SRTBlock] = [seq[0]]
    for cur in seq[1:]:
        prev = out[-1]
        cur_txt = cur.text.strip()
        if (
            cur_txt
            and len(cur_txt) <= max_len
            and cur_txt not in exc
            and prev.end == cur.start
        ):
            prev.merge_with_next(cur)
        else:
            out.append(cur)
    return out


_I_CONTRACTION_RE = re.compile(r"\bi\s*'\s*(m|ve|ll|d|re|s)\b", flags=re.IGNORECASE)
_I_BARE_RE = re.compile(r"\bi\b")


def normalize_english_pronoun_i(blocks: Iterable[SRTBlock]) -> List[SRTBlock]:
    """Normalize English first-person singular pronoun casing.

    Converts:
      - 'i'   -> 'I'
      - "i'm" -> "I'm" (also handles spacing like "i ' m")

    This is a mechanical post-process for STT output.
    """

    def _fix(text: str) -> str:
        # i'xx contractions
        def repl(m: re.Match[str]) -> str:
            suffix = m.group(1).lower()
            mapping = {
                "m": "I'm",
                "ve": "I've",
                "ll": "I'll",
                "d": "I'd",
                "re": "I'm",  # uncommon; best-effort
                "s": "I's",    # uncommon; keep mechanical
            }
            return mapping.get(suffix, "I" + "'" + suffix)

        text = _I_CONTRACTION_RE.sub(repl, text)
        # bare i
        text = _I_BARE_RE.sub("I", text)
        return text

    seq = list(blocks)
    for b in seq:
        if not b.lines:
            continue
        # Preserve multi-line blocks if present.
        b.lines = [_fix(line) for line in b.lines]
    return seq


def _timestamp_to_seconds(ts: str) -> float:
    """Convert an SRT timestamp ``HH:MM:SS,mmm`` to seconds (float)."""

    hh, mm, rest = ts.split(":")
    ss, ms = rest.split(",", 1)
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def _seconds_to_timestamp(sec: float) -> str:
    """Convert seconds (float) back to ``HH:MM:SS,mmm`` timestamp.

    Negative values are clamped to 0.
    """

    if sec < 0:
        sec = 0.0
    total_ms = int(round(sec * 1000))
    total_s, ms = divmod(total_ms, 1000)
    total_m, s = divmod(total_s, 60)
    h, m = divmod(total_m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def fill_short_gaps(blocks: Iterable[SRTBlock], threshold: float = 0.8) -> List[SRTBlock]:
    """Fill short gaps between consecutive blocks by extending the previous end.

    For each pair of consecutive blocks (current, next), if

        0 < (next.start - current.end) < ``threshold`` (in seconds),

    then ``current.end`` is set to ``next.start`` so that the subtitle
    does not disappear briefly before the next one appears.

    Overlapping or zero-length gaps (<= 0) are left untouched.
    """

    seq: List[SRTBlock] = list(blocks)
    if len(seq) < 2:
        return seq

    for i in range(len(seq) - 1):
        cur = seq[i]
        nxt = seq[i + 1]

        try:
            cur_end = _timestamp_to_seconds(cur.end)
            next_start = _timestamp_to_seconds(nxt.start)
        except Exception:
            # If timestamps are malformed, skip this pair defensively.
            continue

        gap = next_start - cur_end
        if 0 < gap < threshold:
            # Extend current block to end exactly when the next one starts.
            cur.end = nxt.start

    return seq


def _target_duration_for_text(text: str, base_min: float) -> float:
    """Compute a target display duration based on text length.

    This keeps "light" interjections short while giving longer sentences
    a bit more time on screen without overdoing it.
    """

    stripped = text.replace("\n", " ").strip()
    if not stripped:
        return base_min

    length = len(stripped)

    # Very short (OK, Yeah, Hello...)
    if length <= 10:
        return base_min
    # Short sentence fragments.
    if length <= 25:
        return max(base_min, 1.3)
    # Medium-length lines.
    if length <= 40:
        return max(base_min, 1.7)
    # Longer lines cap out at ~2.0s to avoid huge stretches.
    return max(base_min, 2.0)


def enforce_min_duration(blocks: Iterable[SRTBlock], min_seconds: float = 1.0) -> List[SRTBlock]:
    """Ensure each block is shown long enough to be readable.

    For each block, we compute a "target" duration based on the text length
    (with ``min_seconds`` as a hard lower bound) and then try to extend the
    block's end time up to that target without overlapping the next block.

    The last block is left unchanged because we don't know the true media
    end time and over-extending could cause odd artifacts in some players.
    """

    seq: List[SRTBlock] = list(blocks)
    if not seq:
        return seq

    for i in range(len(seq)):
        cur = seq[i]

        try:
            start_sec = _timestamp_to_seconds(cur.start)
            end_sec = _timestamp_to_seconds(cur.end)
        except Exception:
            # If timestamps are malformed, skip this block defensively.
            continue

        duration = end_sec - start_sec
        if duration <= 0:
            # Clearly malformed; leave as-is.
            continue

        # Compute a target duration based on text length, but never
        # shorter than min_seconds.
        target = _target_duration_for_text(cur.text, base_min=min_seconds)

        if duration >= target:
            # Already long enough for its content.
            continue

        desired_end = start_sec + target

        # If there's a next block, we must not run past its start.
        if i < len(seq) - 1:
            try:
                next_start = _timestamp_to_seconds(seq[i + 1].start)
            except Exception:
                next_start = desired_end
            new_end_sec = min(desired_end, next_start)
        else:
            # Last block: keep as-is to avoid over-extending beyond media.
            new_end_sec = end_sec

        # Do not shorten blocks accidentally.
        if new_end_sec > end_sec:
            cur.end = _seconds_to_timestamp(new_end_sec)

    return seq


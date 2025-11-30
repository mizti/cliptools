from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple


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

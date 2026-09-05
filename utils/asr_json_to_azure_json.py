#!/usr/bin/env python
"""Convert normalized ASR JSON into the repository's Azure-like JSON.

The sentence-based SRT generator expects an array of phrase records containing
segment display text and word-level timestamps. Local ASR adapters normalize
their output to ``segments`` and ``word_segments`` before calling this module.

All phrases are assigned to one speaker because the local WhisperKit path does
not perform speaker diarization.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _pt_seconds(seconds: float) -> str:
    seconds = max(0.0, seconds)
    value = f"{seconds:.6f}".rstrip("0").rstrip(".")
    return f"PT{value}S"


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalize_words(source_words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for source_word in source_words:
        start = _as_float(source_word.get("start"))
        end = _as_float(source_word.get("end"))
        text = str(source_word.get("word") or "").strip()
        if start is None or end is None or end <= start or not text:
            continue
        normalized.append({"start": start, "end": end, "word": text})
    normalized.sort(key=lambda word: (word["start"], word["end"]))
    return normalized


def _to_phrase_word(word: dict[str, Any]) -> dict[str, str]:
    return {
        "word": word["word"],
        "offset": _pt_seconds(word["start"]),
        "duration": _pt_seconds(word["end"] - word["start"]),
    }


def convert(asr_result: dict[str, Any], speaker: int = 1) -> list[dict[str, Any]]:
    segments = asr_result.get("segments") or []
    word_segments = asr_result.get("word_segments") or []
    normalized_words = _normalize_words(word_segments)

    phrases: list[dict[str, Any]] = []
    word_index = 0
    tolerance = 0.01

    for source_segment in segments:
        start = _as_float(source_segment.get("start"))
        end = _as_float(source_segment.get("end"))
        if start is None or end is None or end <= start:
            continue

        if "words" in source_segment:
            # WhisperKit already records the owning segment for each word. Use
            # that relationship directly so overlapping segment time ranges do
            # not duplicate words in adjacent phrase records.
            segment_words = _normalize_words(source_segment.get("words") or [])
        else:
            # Keep the converter useful for normalized ASR producers that only
            # provide a top-level word stream.
            while (
                word_index < len(normalized_words)
                and normalized_words[word_index]["end"] < start - tolerance
            ):
                word_index += 1

            segment_words = []
            candidate_index = word_index
            while (
                candidate_index < len(normalized_words)
                and normalized_words[candidate_index]["start"] <= end + tolerance
            ):
                word = normalized_words[candidate_index]
                if word["end"] >= start - tolerance:
                    segment_words.append(word)
                candidate_index += 1

        phrase_words = [_to_phrase_word(word) for word in segment_words]

        phrases.append(
            {
                "speaker": int(speaker),
                "offset": _pt_seconds(start),
                "duration": _pt_seconds(end - start),
                "nBest": [
                    {
                        "display": str(source_segment.get("text") or "").strip(),
                        "words": phrase_words,
                    }
                ],
            }
        )

    return phrases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="normalized ASR JSON")
    parser.add_argument("output", type=Path, help="output internal Azure-like JSON")
    parser.add_argument("--speaker", type=int, default=1)
    args = parser.parse_args()

    asr_result = json.loads(args.input.read_text(encoding="utf-8"))
    converted = convert(asr_result, speaker=args.speaker)
    args.output.write_text(
        json.dumps(converted, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(converted)} phrase records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
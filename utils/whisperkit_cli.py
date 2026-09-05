#!/usr/bin/env python
"""Run WhisperKit CLI and normalize its report to the shared ASR schema."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _join_words(words: list[dict[str, Any]], language: str) -> str:
    separator = "" if language.split("-", 1)[0] in {"ja", "zh", "ko"} else " "
    return separator.join(word["word"] for word in words)


def _word_bounds(segment: dict[str, Any]) -> tuple[float, float] | None:
    words = segment["words"]
    if not words:
        return None
    return words[0]["start"], words[-1]["end"]


def _drop_contained_segments(
    segments: list[dict[str, Any]], tolerance: float = 0.02
) -> list[dict[str, Any]]:
    """Drop shorter hypotheses wholly contained by another word-timed segment."""
    ordered = sorted(
        segments,
        key=lambda segment: (
            (_word_bounds(segment) or (segment["start"], segment["end"]))[0],
            -(_word_bounds(segment) or (segment["start"], segment["end"]))[1],
        ),
    )
    kept: list[dict[str, Any]] = []
    kept_bounds: list[tuple[float, float] | None] = []
    for segment in ordered:
        bounds = _word_bounds(segment)
        if bounds is not None and any(
            existing is not None
            and existing[0] <= bounds[0] + tolerance
            and existing[1] >= bounds[1] - tolerance
            for existing in kept_bounds
        ):
            continue
        kept.append(segment)
        kept_bounds.append(bounds)
    return sorted(kept, key=lambda segment: (segment["start"], segment["end"]))


def build_common_result(report: dict[str, Any]) -> dict[str, Any]:
    """Convert one WhisperKit ``TranscriptionResult`` report."""
    segments: list[dict[str, Any]] = []
    word_segments: list[dict[str, Any]] = []
    language = str(report.get("language") or "")
    duration = _as_float((report.get("timings") or {}).get("inputAudioSeconds"))

    for source_segment in report.get("segments") or []:
        words: list[dict[str, Any]] = []
        source_words = source_segment.get("words") or []
        for source_word in source_words:
            start = _as_float(source_word.get("start"))
            end = _as_float(source_word.get("end"))
            text = str(source_word.get("word") or "").strip()
            if not text or start is None or end is None or end <= start:
                continue
            if duration is not None and start >= duration:
                continue
            if duration is not None:
                end = min(end, duration)
            word: dict[str, Any] = {"word": text, "start": start, "end": end}
            score = _as_float(source_word.get("probability"))
            if score is not None:
                word["score"] = score
            words.append(word)
        words.sort(key=lambda word: (word["start"], word["end"]))

        start = _as_float(source_segment.get("start"))
        end = _as_float(source_segment.get("end"))
        if duration is not None and start is not None and start >= duration:
            continue
        if source_words and not words:
            continue
        if start is None and words:
            start = words[0]["start"]
        if end is None and words:
            end = words[-1]["end"]
        if duration is not None and end is not None:
            end = min(end, duration)
        if start is None or end is None or end <= start:
            continue

        text = _join_words(words, language)
        if not text:
            text = str(source_segment.get("text") or "").strip()

        segments.append(
            {
                "start": start,
                "end": end,
                "text": text,
                "words": words,
            }
        )

    # Incremental VAD windows can occasionally emit a short competing
    # hypothesis wholly inside a longer segment. Keeping both creates
    # duplicate, out-of-order subtitles, so retain the complete hypothesis.
    segments = _drop_contained_segments(segments)
    for segment in segments:
        word_segments.extend(dict(word) for word in segment["words"])

    return {
        "segments": segments,
        "word_segments": word_segments,
        "language": language,
    }


def transcribe(
    audio: Path,
    *,
    executable: str,
    model: str,
    language: str,
    incremental_loading: bool,
) -> tuple[dict[str, Any], subprocess.CompletedProcess[str]]:
    resolved_executable = shutil.which(executable)
    if resolved_executable is None:
        raise RuntimeError(
            f"WhisperKit CLI not found: {executable} (install with: brew install whisperkit-cli)"
        )

    with tempfile.TemporaryDirectory(prefix="cliptools-whisperkit-") as report_dir:
        command = [
            resolved_executable,
            "transcribe",
            "--audio-path",
            str(audio.resolve()),
            "--model",
            model,
            "--language",
            language,
            "--word-timestamps",
            "--use-prefill-prompt",
            "--skip-special-tokens",
            "--report",
            "--report-path",
            report_dir,
        ]
        if incremental_loading:
            command.append("--incremental-loading")

        completed = subprocess.run(command, text=True, capture_output=True)
        if completed.returncode != 0:
            details = "\n".join(
                part.strip()
                for part in (completed.stdout, completed.stderr)
                if part and part.strip()
            )
            raise RuntimeError(
                f"WhisperKit CLI exited with status {completed.returncode}"
                + (f":\n{details}" if details else "")
            )
        reports = sorted(Path(report_dir).glob("*.json"))
        if len(reports) != 1:
            raise RuntimeError(
                f"Expected one WhisperKit JSON report in {report_dir}, found {len(reports)}"
            )
        report = json.loads(reports[0].read_text(encoding="utf-8"))
        return report, completed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--executable", default="whisperkit-cli")
    parser.add_argument("--model", default="large-v3-v20240930_626MB")
    parser.add_argument("--language", default="en")
    parser.add_argument(
        "--no-incremental-loading",
        action="store_false",
        dest="incremental_loading",
        help="Load the complete audio file instead of bounded-memory VAD chunks",
    )
    parser.set_defaults(incremental_loading=True)
    args = parser.parse_args()

    report, completed = transcribe(
        args.audio,
        executable=args.executable,
        model=args.model,
        language=args.language,
        incremental_loading=args.incremental_loading,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    common = build_common_result(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(common, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(common['word_segments'])} words to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
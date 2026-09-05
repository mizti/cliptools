#!/usr/bin/env python
"""Run whisper-timestamped and write the common WhisperX-like JSON schema."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SAMPLE_RATE = 16_000


def _as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _join_words(words: list[dict[str, Any]], language: str) -> str:
    separator = "" if language in {"ja", "zh", "ko"} else " "
    return separator.join(word["word"] for word in words)


def build_common_result(
    result: dict[str, Any], audio_duration: float | None = None
) -> dict[str, Any]:
    """Normalize whisper-timestamped output for the existing converter."""
    language = str(result.get("language") or "")
    duration = _as_float(audio_duration)
    segments: list[dict[str, Any]] = []
    word_segments: list[dict[str, Any]] = []

    for source_segment in result.get("segments") or []:
        if not isinstance(source_segment, dict):
            continue

        segment_start = _as_float(source_segment.get("start"))
        segment_end = _as_float(source_segment.get("end"))
        if segment_start is None or segment_end is None:
            continue
        if duration is not None:
            if segment_start >= duration:
                continue
            segment_end = min(segment_end, duration)
        if segment_end <= segment_start:
            continue

        source_words = source_segment.get("words") or []
        words: list[dict[str, Any]] = []
        for source_word in source_words:
            if not isinstance(source_word, dict):
                continue
            text = str(source_word.get("text", source_word.get("word", ""))).strip()
            start = _as_float(source_word.get("start"))
            end = _as_float(source_word.get("end"))
            if not text or start is None or end is None:
                continue
            if duration is not None:
                if start >= duration:
                    continue
                end = min(end, duration)
            if end <= start:
                continue

            word: dict[str, Any] = {"word": text, "start": start, "end": end}
            confidence = _as_float(source_word.get("confidence"))
            if confidence is not None:
                word["score"] = confidence
            words.append(word)

        if source_words and not words:
            continue

        source_text = _join_words(words, language)
        if not source_text:
            source_text = str(source_segment.get("text") or "").strip()
        if not source_text:
            continue

        segment: dict[str, Any] = {
            "start": segment_start,
            "end": segment_end,
            "text": source_text,
            "words": words,
        }
        segments.append(segment)
        word_segments.extend(dict(word) for word in words)

    return {
        "segments": segments,
        "word_segments": word_segments,
        "language": language,
    }


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested

    import torch

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_model(whisper: Any, model_name: str, device: str) -> Any:
    """Load a model while avoiding PyTorch's unsupported SparseMPS transfer."""
    if device != "mps":
        return whisper.load_model(model_name, device=device)

    model = whisper.load_model(model_name, device="cpu")
    alignment_heads = getattr(model, "alignment_heads", None)
    if alignment_heads is not None:
        del model.alignment_heads
    model = model.to("mps")
    if alignment_heads is not None:
        # whisper-timestamped only needs the sparse mask's CPU indices when it
        # selects captured attention heads; it is not part of model inference.
        model.alignment_heads = alignment_heads
    return model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run whisper-timestamped and emit WhisperX-like JSON"
    )
    parser.add_argument("audio", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", default="turbo")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    parser.add_argument("--vad", choices=("none", "silero", "auditok"), default="silero")
    parser.add_argument("--beam-size", type=int, default=5)
    parser.add_argument("--condition-on-previous-text", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.beam_size < 1:
        raise SystemExit("--beam-size must be at least 1")
    if not args.audio.is_file():
        raise SystemExit(f"Audio file not found: {args.audio}")

    import whisper_timestamped as whisper

    device = resolve_device(args.device)
    audio = whisper.load_audio(str(args.audio))
    model = load_model(whisper, args.model, device)
    result = whisper.transcribe(
        model,
        audio,
        language=None if args.language == "auto" else args.language,
        vad=False if args.vad == "none" else args.vad,
        beam_size=args.beam_size,
        temperature=0.0,
        compute_word_confidence=True,
        remove_empty_words=True,
        condition_on_previous_text=args.condition_on_previous_text,
        verbose=False,
    )
    common_result = build_common_result(result, len(audio) / SAMPLE_RATE)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(common_result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {len(common_result['segments'])} segments / "
        f"{len(common_result['word_segments'])} words "
        f"with whisper-timestamped on {device}"
    )


if __name__ == "__main__":
    main()

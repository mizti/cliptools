#!/usr/bin/env python
"""Convert whisperx JSON output into the repo's internal Azure-like JSON.

This repository's sentence-based SRT generator (`utils/json_to_srt_sentences.py`) expects
an array of Azure STT-like phrase records:

  {
    "speaker": 1,
    "offset": "PT1.23S",
    "duration": "PT0.80S",
    "nBest": [
      {
        "display": "...",
        "words": [
          {"word": "Hello", "offset": "PT1.23S", "duration": "PT0.20S"},
          ...
        ]
      }
    ]
  }

WhisperX (with alignment enabled) typically outputs:
  - segments: segment-level start/end + text
  - word_segments: word-level start/end + word

We map each whisperx segment to one phrase record and attach overlapping words.

Usage:
  python -m utils.whisperx_json_to_azure_json in_whisperx.json out_azure.json

Notes:
- This converter intentionally assigns all phrases to speaker=1 (no diarization).
- Timestamps are preserved with microsecond precision in the PT...S format.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _pt_seconds(sec: float) -> str:
    if sec < 0:
        sec = 0.0
    s = f"{sec:.6f}".rstrip("0").rstrip(".")
    return f"PT{s}S"


def _as_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def convert(whisperx: Dict[str, Any], speaker: int = 1) -> List[Dict[str, Any]]:
    segments = whisperx.get("segments") or []
    word_segments = whisperx.get("word_segments") or []

    words_norm: List[Dict[str, Any]] = []
    for w in word_segments:
        ws = _as_float(w.get("start"))
        we = _as_float(w.get("end"))
        if ws is None or we is None:
            continue
        word = (w.get("word") or "").strip()
        if not word:
            continue
        words_norm.append({"start": ws, "end": we, "word": word})
    words_norm.sort(key=lambda d: (d["start"], d["end"]))

    out: List[Dict[str, Any]] = []

    wi = 0
    n_words = len(words_norm)
    tol = 0.01  # 10ms tolerance

    for seg in segments:
        s0 = _as_float(seg.get("start"))
        s1 = _as_float(seg.get("end"))
        if s0 is None or s1 is None:
            continue
        if s1 < s0:
            s0, s1 = s1, s0

        text = (seg.get("text") or "").strip()
        duration = max(0.0, s1 - s0)

        while wi < n_words and words_norm[wi]["end"] < s0 - tol:
            wi += 1

        seg_words: List[Dict[str, Any]] = []
        wj = wi
        while wj < n_words and words_norm[wj]["start"] <= s1 + tol:
            w = words_norm[wj]
            if w["end"] >= s0 - tol and w["start"] <= s1 + tol:
                wdur = max(0.0, w["end"] - w["start"])
                seg_words.append(
                    {
                        "word": w["word"],
                        "offset": _pt_seconds(w["start"]),
                        "duration": _pt_seconds(wdur),
                    }
                )
            wj += 1

        out.append(
            {
                "speaker": int(speaker),
                "offset": _pt_seconds(s0),
                "duration": _pt_seconds(duration),
                "nBest": [{"display": text, "words": seg_words}],
            }
        )

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path, help="whisperx output JSON")
    ap.add_argument("output", type=Path, help="output internal azure-like JSON")
    ap.add_argument("--speaker", type=int, default=1)
    args = ap.parse_args()

    whisperx = json.loads(args.input.read_text(encoding="utf-8"))
    converted = convert(whisperx, speaker=args.speaker)
    args.output.write_text(
        json.dumps(converted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Wrote {len(converted)} phrase records to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

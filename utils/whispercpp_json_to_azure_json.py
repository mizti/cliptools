#!/usr/bin/env python
"""Convert whisper.cpp (whisper-cli) JSON output into Azure-STT-like JSON.

This module exists to reuse the existing Azure-based SRT pipeline:
`python -m utils.json_to_srt_sentences <azure-stt.json> <speaker_id>`.

Input
-----
- whisper-cli JSON file (contains `transcription[]`, each with `offsets` in ms
  and `tokens[]` containing per-token offsets).

Output
------
- Azure-like JSON array: a list of records with `speaker`, `offset`, `duration`,
  and `nBest[0].display/words`.

Token normalization
-------------------
- Drop special tokens like `[_BEG_]` and `[_TT_50]`.
- Merge punctuation tokens into the previous word token.
- Strip leading/trailing whitespace.

Usage
-----
python -m utils.whispercpp_json_to_azure_json in_whisper.json out_azure.json

"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List


_PUNCT_ONLY_RE = re.compile(r"^[\.,!?;:]+$")
_SPECIAL_TOKEN_RE = re.compile(r"^\[_.*\]$")


@dataclass
class WordToken:
    text: str
    start_ms: int
    end_ms: int


def ms_to_pt(ms: int) -> str:
    sec = ms / 1000.0
    s = f"{sec:.3f}".rstrip("0").rstrip(".")
    return f"PT{s}S"


def _is_special_token(t: str) -> bool:
    return bool(_SPECIAL_TOKEN_RE.match(t.strip()))


def _is_punct_only(t: str) -> bool:
    return bool(_PUNCT_ONLY_RE.match(t.strip()))


def iter_word_tokens_from_segment(seg: dict[str, Any]) -> List[WordToken]:
    tokens: List[dict[str, Any]] = list(seg.get("tokens") or [])
    out: List[WordToken] = []

    for tok in tokens:
        raw = str(tok.get("text") or "")
        t = raw.strip()
        if not t:
            continue
        if _is_special_token(t):
            continue

        offsets = tok.get("offsets") or {}
        start = offsets.get("from")
        end = offsets.get("to")
        if start is None or end is None:
            continue
        try:
            start_ms = int(start)
            end_ms = int(end)
        except Exception:
            continue

        if _is_punct_only(t) and out:
            out[-1].text += t
            out[-1].end_ms = max(out[-1].end_ms, end_ms)
            continue

        out.append(WordToken(text=t, start_ms=start_ms, end_ms=end_ms))

    return out


def build_azure_like_records(whisper_json: dict[str, Any], speaker: int = 1) -> List[dict[str, Any]]:
    records: List[dict[str, Any]] = []
    for seg in whisper_json.get("transcription") or []:
        offsets = seg.get("offsets") or {}
        seg_start = offsets.get("from")
        seg_end = offsets.get("to")
        if seg_start is None or seg_end is None:
            continue
        try:
            seg_start_ms = int(seg_start)
            seg_end_ms = int(seg_end)
        except Exception:
            continue
        if seg_end_ms <= seg_start_ms:
            continue

        seg_text = str(seg.get("text") or "").strip()
        if not seg_text:
            continue

        word_tokens = iter_word_tokens_from_segment(seg)
        words: List[dict[str, Any]] = []
        for wt in word_tokens:
            if wt.end_ms < wt.start_ms:
                continue
            words.append(
                {
                    "word": wt.text,
                    "offset": ms_to_pt(wt.start_ms),
                    "duration": ms_to_pt(max(0, wt.end_ms - wt.start_ms)),
                }
            )

        records.append(
            {
                "speaker": speaker,
                "offset": ms_to_pt(seg_start_ms),
                "duration": ms_to_pt(seg_end_ms - seg_start_ms),
                "nBest": [
                    {
                        "display": seg_text,
                        "words": words,
                    }
                ],
            }
        )

    return records


def main(argv: List[str]) -> int:
    if len(argv) != 3:
        print(
            "Usage: python -m utils.whispercpp_json_to_azure_json <in_whisper.json> <out_azure.json>",
            file=sys.stderr,
        )
        return 2

    in_path = Path(argv[1])
    out_path = Path(argv[2])

    obj = json.loads(in_path.read_text(encoding="utf-8"))
    records = build_azure_like_records(obj, speaker=1)
    out_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out_path} ({len(records)} segments)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

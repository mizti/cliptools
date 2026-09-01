from __future__ import annotations

import torch

import whisperx.alignment as alignment
import utils.whisperx_cli as whisperx_cli
from utils.whisperx_cli import (
    conservative_backtrack,
    end_anchored_backtrack,
    patch_alignment,
)


def test_end_anchored_backtrack_skips_trailing_blank_frames() -> None:
    probabilities = torch.tensor(
        [
            [0.9, 0.1],
            [0.1, 0.9],
            [0.9, 0.1],
            [0.9, 0.1],
            [0.9, 0.1],
        ],
        dtype=torch.float32,
    )
    emission = probabilities.log()
    tokens = [1]
    trellis = alignment.get_trellis(emission, tokens, blank_id=0)

    path = end_anchored_backtrack(trellis, emission, tokens, blank_id=0)

    assert path is not None
    assert path[0].time_index == 1
    assert path[-1].time_index == 1


def test_conservative_backtrack_falls_back_only_for_large_trailing_gap() -> None:
    probabilities = torch.tensor(
        [[0.9, 0.1], [0.1, 0.9]] + [[0.9, 0.1]] * 58,
        dtype=torch.float32,
    )
    emission = probabilities.log()
    tokens = [1]
    trellis = alignment.get_trellis(emission, tokens, blank_id=0)
    original = whisperx_cli._UPSTREAM_BACKTRACK
    try:
        near_end_path = [alignment.Point(0, 59, 0.9)]
        whisperx_cli._UPSTREAM_BACKTRACK = lambda *args: near_end_path
        assert conservative_backtrack(trellis, emission, tokens) is near_end_path

        early_path = [alignment.Point(0, 1, 0.9)]
        whisperx_cli._UPSTREAM_BACKTRACK = lambda *args: early_path
        assert conservative_backtrack(trellis, emission, tokens) == (
            end_anchored_backtrack(trellis, emission, tokens)
        )
    finally:
        whisperx_cli._UPSTREAM_BACKTRACK = original


def test_patch_alignment_is_process_local_monkeypatch() -> None:
    original = alignment.backtrack
    try:
        patch_alignment()
        assert alignment.backtrack is conservative_backtrack
    finally:
        alignment.backtrack = original

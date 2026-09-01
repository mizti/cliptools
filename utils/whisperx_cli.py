"""WhisperX CLI with a conservative end-anchored CTC fallback.

WhisperX 3.8 chooses the highest-scoring completion frame for forced alignment.
For merged VAD chunks, that can leave a speech-bearing suffix unused and place
later subtitle text on similar earlier audio. This wrapper retains the upstream
path normally and uses an end-anchored path only when over one second of the
merged VAD chunk would otherwise remain unaligned.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import torch

import whisperx.alignment as alignment


logger = logging.getLogger(__name__)

# Wav2Vec2 emissions are approximately 50 frames per second. A normal Silero
# VAD suffix is much shorter than this; a larger gap means the standard path has
# likely ignored speech near the end of the merged chunk.
MAX_UNALIGNED_TRAILING_FRAMES = 50
_UPSTREAM_BACKTRACK = alignment.backtrack


def end_anchored_backtrack(
    trellis: torch.Tensor,
    emission: torch.Tensor,
    tokens: Sequence[int],
    blank_id: int = 0,
):
    """Backtrack a WhisperX 3.8 trellis from its final time frame."""
    token_position = trellis.size(1) - 1
    path = []
    reached_last_token = False

    for time_position in range(trellis.size(0) - 1, 0, -1):
        stayed = (
            trellis[time_position - 1, token_position]
            + emission[time_position - 1, blank_id]
        )
        changed = (
            trellis[time_position - 1, token_position - 1]
            + emission[time_position - 1, tokens[token_position - 1]]
        )
        token_changed = changed > stayed
        if token_changed:
            reached_last_token = True

        # The end constraint should influence the dynamic-programming path, but
        # trailing blank frames must not inflate the final word's duration.
        if reached_last_token:
            emitted_token = (
                tokens[token_position - 1] if token_changed else blank_id
            )
            probability = emission[time_position - 1, emitted_token].exp().item()
            path.append(
                alignment.Point(token_position - 1, time_position - 1, probability)
            )

        if token_changed:
            token_position -= 1
            if token_position == 0:
                break
    else:
        return None

    return path[::-1]


def conservative_backtrack(
    trellis: torch.Tensor,
    emission: torch.Tensor,
    tokens: Sequence[int],
    blank_id: int = 0,
):
    """Use upstream alignment unless it leaves over one second unaligned."""
    standard_path = _UPSTREAM_BACKTRACK(trellis, emission, tokens, blank_id)
    if standard_path:
        trailing_frames = emission.size(0) - 1 - standard_path[-1].time_index
        if trailing_frames <= MAX_UNALIGNED_TRAILING_FRAMES:
            return standard_path
        logger.info(
            "Using end-anchored alignment fallback: upstream path left %d trailing frames",
            trailing_frames,
        )
    return end_anchored_backtrack(trellis, emission, tokens, blank_id)


def patch_alignment() -> None:
    """Install the conservative backtrack for this WhisperX process only."""
    alignment.backtrack = conservative_backtrack


def main() -> None:
    patch_alignment()
    from whisperx.__main__ import cli

    cli()


if __name__ == "__main__":
    main()

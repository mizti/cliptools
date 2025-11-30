from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Word:
    text: str
    start: float  # seconds
    end: float    # seconds

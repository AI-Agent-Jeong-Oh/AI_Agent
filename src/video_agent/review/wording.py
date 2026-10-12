"""Small display tidying of model wording (the saved text is never changed)."""

from __future__ import annotations

import re

_RANGE_SECONDS = re.compile(r"(\d+)\.\d+(\s*[~–-]\s*\d+(?:\.\d+)?\s*초)")
_SECONDS = re.compile(r"(\d+)\.\d+(\s*초)")


def tidy_seconds(text: str) -> str:
    """"6.0초 프레임", "5.005~35.035초" in model text read as "6초", "5~35초" (whole seconds,
    like the player and the candidate list)."""
    return _SECONDS.sub(r"\1\2", _RANGE_SECONDS.sub(r"\1\2", text))

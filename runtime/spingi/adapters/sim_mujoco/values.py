"""Validation of the values a scene file puts into the simulator model: names go into XML attributes, numbers into
geometry. Shared by the scene and terrain builders."""

from __future__ import annotations

import math
import re
from typing import Any


class SceneError(ValueError):
    """The scene file contains a value that cannot go into the simulator model."""


_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def name(value: Any, what: str) -> str:
    """Names end up in XML attributes: only letters, digits, `_` and `-` (no quotes, angle brackets, spaces)."""
    if not isinstance(value, str) or not _NAME.match(value):
        raise SceneError(f"invalid {what} name {value!r}: use letters, digits, '_' or '-', at most 64 characters")
    return value


def num(value: Any, what: str, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SceneError(f"{what} must be a finite number, got {value!r}")
    if positive and value <= 0:
        raise SceneError(f"{what} must be > 0, got {value!r}")
    return float(value)


def interval(value: Any, what: str) -> tuple[float, float]:
    """`[a, b]` with a < b, in metres."""
    if not isinstance(value, list) or len(value) != 2:
        raise SceneError(f"{what} must be a list of two numbers [from, to]")
    a, b = num(value[0], what), num(value[1], what)
    if not a < b:
        raise SceneError(f"{what} must go from the smaller to the larger value, got {value!r}")
    return a, b


def color(value: Any, what: str) -> tuple[float, float, float]:
    """`#rrggbb` to an RGB triple in 0..1."""
    if not isinstance(value, str) or not _COLOR.match(value):
        raise SceneError(f"{what} must be a colour like '#a0b1c2', got {value!r}")
    return tuple(int(value[i : i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]

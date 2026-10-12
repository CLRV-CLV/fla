"""Small, platform-independent classifier for single-finger page swipes."""
from __future__ import annotations


MIN_SWIPE_DISTANCE = 80.0
MIN_SWIPE_DURATION = 0.035
MAX_SWIPE_DURATION = 1.2
HORIZONTAL_DOMINANCE = 1.35
MAX_VERTICAL_DISTANCE = 160.0


def classify_swipe(start, end, duration_s: float) -> str | None:
    """Return ``next`` for a left swipe, ``prev`` for a right swipe, else None.

    The recognizer is intentionally conservative: short taps, slow drags, and
    predominantly vertical movement are left to the application under the
    touch point. Callers must also exclude controls and drawing tools.
    """
    if duration_s < MIN_SWIPE_DURATION or duration_s > MAX_SWIPE_DURATION:
        return None
    dx = float(end[0]) - float(start[0])
    dy = float(end[1]) - float(start[1])
    if abs(dx) < MIN_SWIPE_DISTANCE or abs(dy) > MAX_VERTICAL_DISTANCE:
        return None
    if abs(dx) < max(1.0, abs(dy)) * HORIZONTAL_DOMINANCE:
        return None
    return "next" if dx < 0 else "prev"

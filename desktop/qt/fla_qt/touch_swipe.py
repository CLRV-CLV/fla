"""Platform-independent touch swipe detection for Windows' mouse-compatibility stream."""
from __future__ import annotations

MIN_SWIPE_DISTANCE = 80.0
MIN_SWIPE_DURATION = 0.035
MAX_SWIPE_DURATION = 1.2
HORIZONTAL_DOMINANCE = 1.35
MAX_VERTICAL_DISTANCE = 160.0

TOUCH_MOUSE_SIGNATURE = 0xFF515700
TOUCH_MOUSE_MASK = 0xFFFFFF00


def is_touch_mouse(extra_info: int) -> bool:
    """Identify Windows touch/pen-generated compatibility mouse input."""
    return (int(extra_info) & TOUCH_MOUSE_MASK) == TOUCH_MOUSE_SIGNATURE


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


class TouchSwipeTracker:
    """Track a marked touch-down through compatibility mouse moves/up.

    Windows drivers are inconsistent about carrying the touch signature on every
    synthesized move and release. Trust the marked down event, tolerate unmarked
    intermediate messages, and require the full path to satisfy swipe thresholds.
    """

    def __init__(self):
        self._start = None

    def begin(self, point, extra_info: int, now: float) -> None:
        self._start = (int(point[0]), int(point[1]), float(now)) if is_touch_mouse(extra_info) else None

    def move(self, now: float) -> None:
        if self._start is not None and float(now) - self._start[2] > MAX_SWIPE_DURATION:
            self._start = None

    def end(self, point, now: float):
        start, self._start = self._start, None
        if start is None:
            return None
        end = (int(point[0]), int(point[1]))
        duration = float(now) - start[2]
        if classify_swipe(start[:2], end, duration) is None:
            return None
        return start[0], start[1], end[0], end[1], duration

    def reset(self) -> None:
        self._start = None

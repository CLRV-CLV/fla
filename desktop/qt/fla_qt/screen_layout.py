"""Pure helpers for keeping top-level windows inside a changed display work area."""
from __future__ import annotations


def fit_window_rect(rect, bounds, *, desired_size=None, min_size=(1, 1), margin=12, old_bounds=None):
    """Return a window rectangle fitted to ``bounds`` and optionally mapped from an old screen.

    Rectangles are ``(x, y, width, height)`` tuples in the same coordinate space. ``desired_size``
    lets callers restore the user's preferred size after a temporary shrink to a smaller display.
    """
    x, y, width, height = (int(v) for v in rect)
    bx, by, bound_width, bound_height = (int(v) for v in bounds)
    if bound_width <= 0 or bound_height <= 0:
        return x, y, max(1, width), max(1, height)

    mx = min(max(0, int(margin)), max(0, (bound_width - 1) // 2))
    my = min(max(0, int(margin)), max(0, (bound_height - 1) // 2))
    available_width = max(1, bound_width - 2 * mx)
    available_height = max(1, bound_height - 2 * my)
    wanted_width, wanted_height = desired_size or (width, height)
    minimum_width, minimum_height = min_size
    minimum_width = min(max(1, int(minimum_width)), available_width)
    minimum_height = min(max(1, int(minimum_height)), available_height)
    width = min(max(minimum_width, int(wanted_width)), available_width)
    height = min(max(minimum_height, int(wanted_height)), available_height)

    if old_bounds is not None:
        old_x, old_y, old_width, old_height = (int(v) for v in old_bounds)
        old_travel_x = old_width - int(rect[2])
        old_travel_y = old_height - int(rect[3])
        rx = 0.5 if old_travel_x <= 0 else max(0.0, min(1.0, (int(rect[0]) - old_x) / old_travel_x))
        ry = 0.5 if old_travel_y <= 0 else max(0.0, min(1.0, (int(rect[1]) - old_y) / old_travel_y))
        x = bx + mx + round(rx * max(0, available_width - width))
        y = by + my + round(ry * max(0, available_height - height))
    else:
        x = min(max(x, bx + mx), bx + bound_width - mx - width)
        y = min(max(y, by + my), by + bound_height - my - height)

    return x, y, width, height

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "desktop" / "qt"))

from fla_qt.screen_layout import fit_window_rect, physical_rect_to_logical


def test_window_is_clamped_inside_smaller_resolution():
    before = (1000, 120, 940, 680)
    old_screen = (0, 0, 1920, 1080)
    new_screen = (0, 0, 1280, 720)
    fitted = fit_window_rect(before, new_screen, desired_size=(940, 680),
                             min_size=(680, 460), margin=8, old_bounds=old_screen)
    x, y, width, height = fitted
    assert width == 940
    assert height == 680
    assert x >= 8 and y >= 8
    assert x + width <= 1280 - 8
    assert y + height <= 720 - 8


def test_preferred_window_size_returns_after_display_grows_again():
    small = (0, 0, 800, 560)
    large = (0, 0, 1920, 1080)
    shrunk = fit_window_rect((0, 0, 940, 680), small, desired_size=(940, 680),
                             min_size=(640, 420), margin=8)
    assert shrunk[2:] == (784, 544)
    restored = fit_window_rect(shrunk, large, desired_size=(940, 680),
                               min_size=(680, 460), margin=8, old_bounds=small)
    assert restored[2:] == (940, 680)
    assert large[0] <= restored[0] <= large[2] - restored[2]
    assert large[1] <= restored[1] <= large[3] - restored[3]


def test_removed_monitor_does_not_leave_window_off_screen():
    fitted = fit_window_rect((2200, 300, 940, 680), (0, 0, 1366, 768),
                             desired_size=(940, 680), min_size=(680, 460), margin=8)
    x, y, width, height = fitted
    assert 0 <= x and 0 <= y
    assert x + width <= 1366
    assert y + height <= 768


def test_physical_rect_maps_relative_to_non_primary_scaled_monitor_origin():
    # A 150%-scaled monitor to the right of a 100% primary display.
    assert physical_rect_to_logical(
        (3000, 120, 900, 600),
        (1920, 0, 1280, 720),
        (2880, 0, 1920, 1080),
        1.5,
    ) == (2000, 80, 600, 400)


def test_physical_rect_maps_negative_origin_monitor():
    assert physical_rect_to_logical(
        (-2400, 150, 1200, 600),
        (-1707, 0, 1707, 960),
        (-2560, 0, 2560, 1440),
        1.5,
    ) == (-1600, 100, 800, 400)

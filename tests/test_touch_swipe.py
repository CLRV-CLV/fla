from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "desktop" / "qt"))

from fla_qt.touch_swipe import TOUCH_MOUSE_SIGNATURE, TouchSwipeTracker, classify_swipe


def test_left_and_right_swipes_map_to_next_and_previous():
    assert classify_swipe((500, 300), (360, 315), 0.25) == "next"
    assert classify_swipe((360, 300), (500, 285), 0.25) == "prev"


def test_taps_vertical_moves_and_slow_drags_are_not_page_swipes():
    assert classify_swipe((500, 300), (470, 305), 0.2) is None
    assert classify_swipe((500, 300), (490, 430), 0.3) is None
    assert classify_swipe((500, 300), (350, 300), 1.5) is None
    assert classify_swipe((500, 300), (350, 300), 0.01) is None


def test_touch_signature_is_recognized_even_when_move_and_release_lose_it():
    tracker = TouchSwipeTracker()
    tracker.begin((500, 300), TOUCH_MOUSE_SIGNATURE | 0x80, 10.0)
    tracker.move(10.15)  # The OS may omit the signature on synthesized WM_MOUSEMOVE.
    result = tracker.end((350, 315), 10.35)
    assert result[:4] == (500, 300, 350, 315)
    assert abs(result[4] - 0.35) < 1e-9


def test_unmarked_tablet_touch_down_can_be_accepted_when_digitizer_is_present():
    tracker = TouchSwipeTracker()
    assert tracker.begin((500, 300), 0, 1.0, allow_unmarked=True)
    tracker.move(1.15)
    result = tracker.end((350, 315), 1.3)
    assert result is not None
    assert classify_swipe(result[:2], result[2:4], result[4]) == "next"


def test_regular_mouse_drags_and_expired_touch_sequences_are_ignored():
    tracker = TouchSwipeTracker()
    assert not tracker.begin((500, 300), 0, 1.0)
    assert tracker.end((350, 300), 1.3) is None

    tracker.begin((500, 300), TOUCH_MOUSE_SIGNATURE | 0x80, 2.0)
    tracker.move(3.25)
    assert tracker.end((350, 300), 3.3) is None

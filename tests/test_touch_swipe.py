from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "desktop" / "qt"))

from fla_qt.touch_swipe import classify_swipe


def test_left_and_right_swipes_map_to_next_and_previous():
    assert classify_swipe((500, 300), (360, 315), 0.25) == "next"
    assert classify_swipe((360, 300), (500, 285), 0.25) == "prev"


def test_taps_vertical_moves_and_slow_drags_are_not_page_swipes():
    assert classify_swipe((500, 300), (470, 305), 0.2) is None
    assert classify_swipe((500, 300), (490, 430), 0.3) is None
    assert classify_swipe((500, 300), (350, 300), 1.5) is None
    assert classify_swipe((500, 300), (350, 300), 0.01) is None

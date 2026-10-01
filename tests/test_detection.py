import numpy as np
import pytest
import cv2

from nottrigger.config import RegionOfInterest, TargetColor
from nottrigger.detection import (
    Detector,
    EdgeTriggerState,
    clamp_roi_to_frame,
    compute_hsv_bounds,
    match_ratio,
)


def test_clamp_roi_fits_inside_smaller_frame():
    roi = RegionOfInterest(x=100, y=100, w=200, h=200)
    clamped = clamp_roi_to_frame(roi, frame_w=150, frame_h=150)
    assert clamped.x < 150 and clamped.y < 150
    assert clamped.x + clamped.w <= 150
    assert clamped.y + clamped.h <= 150


def test_hsv_bounds_simple_no_wraparound():
    target = TargetColor(h=90, s=128, v=128, h_tolerance=10, s_tolerance=20, v_tolerance=20, has_sample=True)
    bounds = compute_hsv_bounds(target)
    assert len(bounds) == 1
    lower, upper = bounds[0]
    assert lower[0] == 80 and upper[0] == 100


def test_hsv_bounds_wraps_low_edge_like_red():
    # Hue near 0 (red) with tolerance pushing below 0 must wrap to the
    # top of the range (179), not silently clip and lose coverage.
    target = TargetColor(h=3, s=200, v=200, h_tolerance=10, s_tolerance=10, v_tolerance=10, has_sample=True)
    bounds = compute_hsv_bounds(target)
    assert len(bounds) == 2
    los = sorted(b[0][0] for b in bounds)
    his = sorted(b[1][0] for b in bounds)
    assert los[0] == 0
    assert his[-1] == 179


def test_hsv_bounds_wraps_high_edge():
    target = TargetColor(h=177, s=200, v=200, h_tolerance=10, s_tolerance=10, v_tolerance=10, has_sample=True)
    bounds = compute_hsv_bounds(target)
    assert len(bounds) == 2
    highs = [b[1][0] for b in bounds]
    assert 179 in highs


def test_match_ratio_all_in_range():
    hsv = np.zeros((10, 10, 3), dtype=np.uint8)
    hsv[..., 0] = 50
    hsv[..., 1] = 150
    hsv[..., 2] = 150
    bounds = [(np.array([40, 100, 100], dtype=np.uint8), np.array([60, 200, 200], dtype=np.uint8))]
    assert match_ratio(hsv, bounds) == pytest.approx(1.0)


def test_match_ratio_half_in_range():
    hsv = np.zeros((10, 10, 3), dtype=np.uint8)
    hsv[:5, :, 0] = 50  # matches
    hsv[5:, :, 0] = 10  # doesn't match
    hsv[..., 1] = 150
    hsv[..., 2] = 150
    bounds = [(np.array([40, 100, 100], dtype=np.uint8), np.array([60, 200, 200], dtype=np.uint8))]
    assert match_ratio(hsv, bounds) == pytest.approx(0.5)


def test_circle_match_ratio_ignores_pixels_outside_circle():
    hsv = np.zeros((9, 9, 3), dtype=np.uint8)
    hsv[..., :] = (50, 200, 200)
    hsv[2:7, 2:7] = (0, 0, 0)
    frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    target = TargetColor(h=50, s=200, v=200, h_tolerance=0, s_tolerance=0, v_tolerance=0, has_sample=True)

    result = Detector().update(
        frame,
        RegionOfInterest(x=4, y=4, w=1, h=1, shape="circle", radius=2),
        target,
        match_threshold=0.01,
        confirm_frames=1,
        cooldown_ms=0,
    )

    assert result.match_ratio == pytest.approx(0.0)


def test_point_region_checks_exactly_one_pixel():
    hsv = np.zeros((5, 5, 3), dtype=np.uint8)
    hsv[3, 2] = (50, 200, 200)
    frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    target = TargetColor(h=50, s=200, v=200, h_tolerance=0, s_tolerance=0, v_tolerance=0, has_sample=True)

    result = Detector().update(
        frame,
        RegionOfInterest(x=2, y=3, w=1, h=1, shape="point"),
        target,
        match_threshold=0.5,
        confirm_frames=1,
        cooldown_ms=0,
    )

    assert result.match_ratio == pytest.approx(1.0)
    assert result.should_fire is True


def test_edge_trigger_requires_confirm_frames():
    state = EdgeTriggerState()
    t = 0.0
    assert state.decide(True, t, confirm_frames=3, cooldown_ms=0) is False
    assert state.decide(True, t + 0.01, confirm_frames=3, cooldown_ms=0) is False
    assert state.decide(True, t + 0.02, confirm_frames=3, cooldown_ms=0) is True


def test_edge_trigger_does_not_refire_until_rearmed():
    state = EdgeTriggerState()
    for i in range(3):
        state.decide(True, i * 0.01, confirm_frames=3, cooldown_ms=0)
    # Still matching, already fired once: must not fire again.
    assert state.decide(True, 0.5, confirm_frames=3, cooldown_ms=0) is False
    # Leaves the ROI color: re-arms.
    state.decide(False, 0.6, confirm_frames=3, cooldown_ms=0)
    # New sustained match: fires again.
    assert state.decide(True, 0.61, confirm_frames=1, cooldown_ms=0) is True


def test_edge_trigger_respects_cooldown():
    state = EdgeTriggerState()
    for i in range(3):
        state.decide(True, i * 0.01, confirm_frames=3, cooldown_ms=1000)
    state.decide(False, 0.5, confirm_frames=3, cooldown_ms=1000)
    # Re-armed, but within the 1000ms cooldown window: must not fire.
    assert state.decide(True, 0.6, confirm_frames=1, cooldown_ms=1000) is False
    # After cooldown has elapsed: fires.
    assert state.decide(True, 1.1, confirm_frames=1, cooldown_ms=1000) is True


def test_edge_trigger_resets_consecutive_count_on_miss():
    state = EdgeTriggerState()
    state.decide(True, 0.0, confirm_frames=3, cooldown_ms=0)
    state.decide(True, 0.01, confirm_frames=3, cooldown_ms=0)
    state.decide(False, 0.02, confirm_frames=3, cooldown_ms=0)  # noise drop
    assert state.consecutive_match == 0

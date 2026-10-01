"""Color-mismatch detection: ROI crop, HSV range match, and firing logic.

Split into small pure functions (no threading, no I/O) plus one small
stateful class, so the firing behavior can be unit-tested with plain
booleans and timestamps instead of a real camera.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from nottrigger.config import RegionOfInterest, TargetColor

HUE_MAX = 179  # OpenCV hue range is 0-179, not 0-359


def clamp_roi_to_frame(roi: RegionOfInterest, frame_w: int, frame_h: int) -> RegionOfInterest:
    """Clip an ROI so it never reads outside the current frame.

    Needed because the ROI is stored in native-frame pixels but the
    camera's resolution can change after the ROI was drawn (e.g. the
    user switches from 1080p to 4K without re-drawing it).
    """
    x = max(0, min(roi.x, max(frame_w - 1, 0)))
    y = max(0, min(roi.y, max(frame_h - 1, 0)))
    w = max(0, min(roi.w, frame_w - x))
    h = max(0, min(roi.h, frame_h - y))
    return RegionOfInterest(x=x, y=y, w=w, h=h)


def compute_hsv_bounds(target: TargetColor) -> list[tuple[np.ndarray, np.ndarray]]:
    """Build one or two (lower, upper) HSV bound pairs for cv2.inRange.

    Hue is circular (0 and 179 are adjacent), so a target hue near either
    edge - most notably red, sitting right at the wraparound - needs two
    ranges OR'd together rather than one that would otherwise clamp
    incorrectly at 0/179 and silently miss half the intended tolerance.
    """
    h, s, v = target.h, target.s, target.v
    h_tol, s_tol, v_tol = target.h_tolerance, target.s_tolerance, target.v_tolerance

    s_lo, s_hi = max(0, s - s_tol), min(255, s + s_tol)
    v_lo, v_hi = max(0, v - v_tol), min(255, v + v_tol)

    h_lo = h - h_tol
    h_hi = h + h_tol

    if h_lo < 0 and h_hi > HUE_MAX:
        # Tolerance wraps around on both sides at once: the whole hue
        # range matches, so a single 0..179 band covers it.
        ranges = [(0, HUE_MAX)]
    elif h_lo < 0:
        ranges = [(0, h_hi), (HUE_MAX + h_lo + 1, HUE_MAX)]
    elif h_hi > HUE_MAX:
        ranges = [(h_lo, HUE_MAX), (0, h_hi - HUE_MAX - 1)]
    else:
        ranges = [(h_lo, h_hi)]

    return [
        (
            np.array([lo, s_lo, v_lo], dtype=np.uint8),
            np.array([hi, s_hi, v_hi], dtype=np.uint8),
        )
        for lo, hi in ranges
    ]


def match_ratio(hsv_roi: np.ndarray, bounds: list[tuple[np.ndarray, np.ndarray]]) -> float:
    """Fraction (0..1) of ROI pixels that fall in any of the HSV bounds."""
    if hsv_roi.size == 0 or not bounds:
        return 0.0
    mask = cv2.inRange(hsv_roi, bounds[0][0], bounds[0][1])
    for lo, hi in bounds[1:]:
        mask = cv2.bitwise_or(mask, cv2.inRange(hsv_roi, lo, hi))
    return float(cv2.countNonZero(mask)) / float(mask.size)


@dataclass
class DetectionResult:
    match_ratio: float
    is_match: bool
    should_fire: bool
    processing_ms: float
    roi_used: RegionOfInterest


@dataclass
class EdgeTriggerState:
    """Tracks the fire/re-arm state machine across frames.

    Behavior (matches the tool's original design):
      - Fires once per "hit" - a rising edge into a sustained match.
      - Requires `confirm_frames` consecutive matching frames before
        firing, to ignore single-frame noise.
      - Will not fire again until the color has left the ROI (armed
        again) - so one flagged product can't trigger twice.
      - `cooldown_ms` is a hard floor between fires, independent of the
        re-arm rule, as a second line of defense against rapid re-firing.
    """

    armed: bool = True
    consecutive_match: int = 0
    last_fire_time: float = field(default_factory=lambda: -1e9)

    def decide(self, is_match: bool, now: float, confirm_frames: int, cooldown_ms: float) -> bool:
        if is_match:
            self.consecutive_match += 1
        else:
            self.consecutive_match = 0
            self.armed = True

        cooldown_elapsed = (now - self.last_fire_time) * 1000.0 >= cooldown_ms
        should_fire = self.armed and self.consecutive_match >= max(1, confirm_frames) and cooldown_elapsed

        if should_fire:
            self.armed = False
            self.last_fire_time = now
        return should_fire

    def reset(self) -> None:
        self.armed = True
        self.consecutive_match = 0
        self.last_fire_time = -1e9


class Detector:
    """Owns the per-frame HSV matching plus the firing state machine."""

    def __init__(self) -> None:
        self.state = EdgeTriggerState()

    def update(
        self,
        frame_bgr: np.ndarray,
        roi: RegionOfInterest,
        target: TargetColor,
        match_threshold: float,
        confirm_frames: int,
        cooldown_ms: float,
        now: float | None = None,
    ) -> DetectionResult:
        t0 = time.perf_counter()
        now = now if now is not None else t0

        frame_h, frame_w = frame_bgr.shape[:2]
        used_roi = clamp_roi_to_frame(roi, frame_w, frame_h)

        if used_roi.is_empty() or not target.has_sample:
            processing_ms = (time.perf_counter() - t0) * 1000.0
            return DetectionResult(0.0, False, False, processing_ms, used_roi)

        crop = frame_bgr[used_roi.y : used_roi.y + used_roi.h, used_roi.x : used_roi.x + used_roi.w]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        bounds = compute_hsv_bounds(target)
        ratio = match_ratio(hsv, bounds)
        is_match = ratio >= match_threshold

        should_fire = self.state.decide(is_match, now, confirm_frames, cooldown_ms)
        processing_ms = (time.perf_counter() - t0) * 1000.0
        return DetectionResult(ratio, is_match, should_fire, processing_ms, used_roi)

    def reset(self) -> None:
        self.state.reset()

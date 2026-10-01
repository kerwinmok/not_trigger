"""Camera device listing and the background capture thread.

Performance design, in one place because it matters for the whole app:

  - Detection always runs on the full-resolution frame the camera just
    produced, in this worker thread, at whatever rate the camera can
    sustain. It never waits on the UI.
    - Preview resizing and color conversion can be disabled or throttled.
  - The UI thread never touches cv2.VideoCapture and the camera thread
    never touches Tkinter; they hand off through LatestBox, a tiny
    single-slot box (not a growing queue) so the UI always sees the
    newest frame and a slow UI tick can never build a backlog.
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

from nottrigger.actions import ActionDispatcher
from nottrigger.config import RegionOfInterest, TargetColor, TriggerAction
from nottrigger.constants import (
    CHEAP_PREVIEW_FPS,
    CHEAP_PREVIEW_MAX_WIDTH,
    LOW_CPU_PREVIEW_FPS,
    LOW_CPU_PREVIEW_MAX_WIDTH,
)
from nottrigger.detection import Detector

logger = logging.getLogger(__name__)


def _default_backend() -> int:
    # DirectShow is the fast, standard choice on Windows (this app's
    # target platform). Elsewhere, let OpenCV pick - mainly so this
    # module still imports and runs its (camera-less) logic on a
    # non-Windows dev machine while writing tests.
    return cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY


@dataclass(frozen=True)
class CameraDevice:
    index: int
    name: str


def list_cameras(max_probe: int = 8) -> list[CameraDevice]:
    """Enumerate available cameras.

    Tries pygrabber first (Windows-only DirectShow device names, which is
    where the *actual* device name - "Logitech BRIO", not "Camera 0" -
    comes from). Falls back to probing indices with OpenCV and labeling
    them generically if pygrabber isn't available or reports nothing.
    """
    try:
        from pygrabber.dshow_graph import FilterGraph

        names = FilterGraph().get_input_devices()
        if names:
            return [CameraDevice(index=i, name=name) for i, name in enumerate(names)]
    except Exception as exc:  # pragma: no cover - Windows-only dependency
        logger.info("pygrabber unavailable or failed (%s); falling back to index probing.", exc)

    backend = _default_backend()
    devices: list[CameraDevice] = []
    for i in range(max_probe):
        cap = cv2.VideoCapture(i, backend)
        try:
            if cap.isOpened():
                devices.append(CameraDevice(index=i, name=f"Camera {i}"))
        finally:
            cap.release()
    return devices


@dataclass
class CameraSettings:
    index: int = 0
    requested_fps: int | None = None
    brightness: float | None = None
    exposure: float | None = None
    buffer_size: int = 1


@dataclass
class FrameResult:
    """One frame's worth of stats, handed off to the UI thread."""

    timestamp: float
    frame_interval_ms: float | None
    match_ratio: float
    is_match: bool
    should_fire: bool
    processing_ms: float
    dispatch_ms: float | None
    roi: RegionOfInterest
    frame_w: int
    frame_h: int
    preview_rgb: np.ndarray | None  # small RGB copy for display, or None
    preview_scale: float  # preview_rgb pixels -> native pixels multiplier


class LatestBox:
    """Thread-safe single-slot mailbox: newest value only, never a backlog."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._value = None

    def set(self, value) -> None:
        with self._lock:
            self._value = value

    def pop(self):
        with self._lock:
            value, self._value = self._value, None
            return value


class CameraError(Exception):
    pass


# Image adjustments apply live (no stream reopen) via these OpenCV
# properties; resolution/fps do not get this treatment because they
# generally need the stream reopened to take effect reliably.
ADJUSTMENT_PROPS = {
    "brightness": cv2.CAP_PROP_BRIGHTNESS,
    "exposure": cv2.CAP_PROP_EXPOSURE,
}


class CameraWorker(threading.Thread):
    """Owns the VideoCapture and runs capture -> detect -> dispatch."""

    def __init__(
        self,
        settings: CameraSettings,
        roi_provider: Callable[[], RegionOfInterest],
        target_provider: Callable[[], TargetColor],
        detection_params_provider: Callable[[], tuple[float, int, float]],
        action_provider: Callable[[], TriggerAction],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="CameraWorker")
        self.settings = settings
        self._roi_provider = roi_provider
        self._target_provider = target_provider
        self._detection_params_provider = detection_params_provider
        self._action_provider = action_provider
        self._on_error = on_error

        self._stop_event = threading.Event()
        self._detector = Detector()
        self._dispatcher = ActionDispatcher()

        self.results = LatestBox()  # FrameResult, consumed by the UI
        self._last_preview_emit = 0.0
        self._preview_enabled = True
        self._low_cpu = False
        self._preview_lock = threading.Lock()

        self.actual_width = 0
        self.actual_height = 0
        self.actual_fps = 0.0

        self._adjustments_lock = threading.Lock()
        self._pending_adjustments: dict[str, float] = {}

    def apply_pending_adjustment(self, key: str, value: float) -> None:
        """Queue a brightness or exposure change.

        Applied from inside the capture loop on the next iteration, since
        cv2.VideoCapture isn't safe to touch from a second thread while
        the capture thread may be mid-read().
        """
        if key not in ADJUSTMENT_PROPS:
            return
        with self._adjustments_lock:
            self._pending_adjustments[key] = value

    def set_preview_options(self, enabled: bool, low_cpu: bool) -> None:
        with self._preview_lock:
            self._preview_enabled = enabled
            self._low_cpu = low_cpu

    def _drain_pending_adjustments(self, cap: cv2.VideoCapture) -> None:
        with self._adjustments_lock:
            pending, self._pending_adjustments = self._pending_adjustments, {}
        for key, value in pending.items():
            cap.set(ADJUSTMENT_PROPS[key], value)

    def stop(self) -> None:
        self._stop_event.set()

    def reset_detector(self) -> None:
        self._detector.reset()

    def run(self) -> None:
        cap = None
        try:
            cap = self._open_capture()
            self._report_actual_mode(cap)
            self._capture_loop(cap)
        except CameraError as exc:
            logger.error("Camera error: %s", exc)
            if self._on_error:
                self._on_error(str(exc))
        finally:
            if cap is not None:
                cap.release()

    def _open_capture(self) -> cv2.VideoCapture:
        backend = _default_backend()
        cap = cv2.VideoCapture(self.settings.index, backend)
        if not cap.isOpened():
            raise CameraError(f"Could not open camera index {self.settings.index}.")

        if self.settings.requested_fps is not None:
            cap.set(cv2.CAP_PROP_FPS, self.settings.requested_fps)

        # Ask the backend to keep as little internal buffer as possible,
        # so we always read the freshest frame rather than an
        # already-stale one queued up behind it. Best-effort: not every
        # backend/driver honors this property.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, max(1, self.settings.buffer_size))

        for prop, value in (
            (cv2.CAP_PROP_BRIGHTNESS, self.settings.brightness),
            (cv2.CAP_PROP_EXPOSURE, self.settings.exposure),
        ):
            if value is not None:
                cap.set(prop, value)

        return cap

    def _report_actual_mode(self, cap: cv2.VideoCapture) -> None:
        self.actual_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.actual_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        reported_fps = cap.get(cv2.CAP_PROP_FPS)
        self.actual_fps = reported_fps if reported_fps and reported_fps > 0 else 0.0

    def _capture_loop(self, cap: cv2.VideoCapture) -> None:
        last_frame_time: float | None = None
        consecutive_failures = 0

        while not self._stop_event.is_set():
            self._drain_pending_adjustments(cap)

            ok, frame = cap.read()
            now = time.perf_counter()

            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures > 30:
                    raise CameraError("Camera stopped returning frames (unplugged or driver crash?).")
                time.sleep(0.01)
                continue
            consecutive_failures = 0

            frame_interval_ms = None
            if last_frame_time is not None:
                frame_interval_ms = (now - last_frame_time) * 1000.0
            last_frame_time = now

            roi = self._roi_provider()
            target = self._target_provider()
            match_threshold, confirm_frames, cooldown_ms = self._detection_params_provider()

            result = self._detector.update(
                frame, roi, target, match_threshold, confirm_frames, cooldown_ms, now
            )

            dispatch_ms = None
            if result.should_fire:
                action = self._action_provider()
                dispatch_ms = self._dispatcher.fire(action)

            preview_rgb, preview_scale = self._maybe_build_preview(frame, now)

            self.results.set(
                FrameResult(
                    timestamp=now,
                    frame_interval_ms=frame_interval_ms,
                    match_ratio=result.match_ratio,
                    is_match=result.is_match,
                    should_fire=result.should_fire,
                    processing_ms=result.processing_ms,
                    dispatch_ms=dispatch_ms,
                    roi=result.roi_used,
                    frame_w=frame.shape[1],
                    frame_h=frame.shape[0],
                    preview_rgb=preview_rgb,
                    preview_scale=preview_scale,
                )
            )

    def _maybe_build_preview(self, frame_bgr: np.ndarray, now: float) -> tuple[np.ndarray | None, float]:
        with self._preview_lock:
            enabled = self._preview_enabled
            low_cpu = self._low_cpu
        if not enabled:
            return None, 1.0

        preview_fps = LOW_CPU_PREVIEW_FPS if low_cpu else CHEAP_PREVIEW_FPS
        max_width = LOW_CPU_PREVIEW_MAX_WIDTH if low_cpu else CHEAP_PREVIEW_MAX_WIDTH
        min_interval = 1.0 / preview_fps
        if now - self._last_preview_emit < min_interval:
            return None, 1.0

        self._last_preview_emit = now

        h, w = frame_bgr.shape[:2]
        scale = 1.0
        if w > max_width:
            scale = max_width / w
            frame_bgr = cv2.resize(
                frame_bgr,
                (max_width, max(1, round(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        return rgb, scale

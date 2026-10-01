"""Latency math and live measurement.

Two different kinds of numbers live in this module, and the rest of the
app (and the README) should always be clear about which kind it's
showing:

1. THEORETICAL numbers that follow from arithmetic alone (frame period
   from fps, pixel counts, raw-bandwidth pressure). These are exact and
   don't depend on any particular camera.

2. MEASURED numbers that come from timestamps taken while the app is
   actually running: how long detection took, how long the input
   dispatch took, and how far apart frames actually arrived (which can
   differ from the requested fps). These are specific to whatever
   camera, USB port, and PC this happens to run on.

Deliberately NOT included: a single "typical webcam latency" constant.
Sensor exposure/readout, USB transfer, and driver buffering add a
variable, hardware-specific delay that isn't something this process can
observe directly - it has to be measured (see SelfTestRecorder below),
not guessed.
"""

from __future__ import annotations

import statistics
import time
from collections import deque
from dataclasses import dataclass


def theoretical_frame_period_ms(fps: float) -> float:
    """Exact time between frames implied by a frame rate alone."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    return 1000.0 / fps


def pixel_count(width: int, height: int) -> int:
    return width * height


def megapixels(width: int, height: int) -> float:
    return pixel_count(width, height) / 1_000_000.0


def raw_yuy2_bandwidth_mbps(width: int, height: int, fps: float) -> float:
    """Theoretical *uncompressed* YUY2 (16 bits/pixel) bandwidth, in Mbit/s.

    This is an illustrative ceiling, not a claim about what any specific
    camera actually sends - most webcams switch to MJPEG (compressed) at
    higher resolutions/frame rates precisely because this raw figure gets
    large enough to strain or exceed USB bandwidth. Use it to show *why*
    resolution matters for a real pipeline even though it cancels out of
    the frame-period math.
    """
    bytes_per_frame = pixel_count(width, height) * 2
    bytes_per_second = bytes_per_frame * fps
    return bytes_per_second * 8 / 1_000_000.0


@dataclass(frozen=True)
class ModeComparison:
    label: str
    width: int
    height: int
    fps: int

    @property
    def frame_period_ms(self) -> float:
        return theoretical_frame_period_ms(self.fps)

    @property
    def megapixels(self) -> float:
        return megapixels(self.width, self.height)

    @property
    def raw_bandwidth_mbps(self) -> float:
        return raw_yuy2_bandwidth_mbps(self.width, self.height, self.fps)


def compare_modes(modes: list[tuple[str, int, int, int]]) -> list[ModeComparison]:
    """Build the exact-math comparison table for a list of (label, w, h, fps)."""
    return [ModeComparison(label, w, h, fps) for label, w, h, fps in modes]


class RollingStat:
    """Fixed-window running stat: mean, spread, and last value.

    Used for anything measured once per frame (frame interval, processing
    time, dispatch time) so the UI shows a stable, recent-history number
    instead of one noisy instantaneous sample.
    """

    __slots__ = ("_values", "_maxlen")

    def __init__(self, maxlen: int = 90) -> None:
        self._maxlen = maxlen
        self._values: deque[float] = deque(maxlen=maxlen)

    def add(self, value: float) -> None:
        self._values.append(value)

    def __len__(self) -> int:
        return len(self._values)

    @property
    def is_empty(self) -> bool:
        return len(self._values) == 0

    @property
    def last(self) -> float | None:
        return self._values[-1] if self._values else None

    @property
    def mean(self) -> float | None:
        if not self._values:
            return None
        return statistics.fmean(self._values)

    @property
    def stdev(self) -> float:
        if len(self._values) < 2:
            return 0.0
        return statistics.pstdev(self._values)

    def reset(self) -> None:
        self._values.clear()


@dataclass
class PipelineLatencyEstimate:
    """A software-observable latency estimate, assembled from live stats."""

    requested_fps: float
    frame_period_ms: float
    achieved_fps: float | None
    frame_interval_jitter_ms: float
    processing_ms: float | None
    dispatch_ms: float | None
    estimated_pipeline_ms: float | None
    sample_count: int

    @property
    def is_ready(self) -> bool:
        return self.sample_count > 0 and self.processing_ms is not None

    NOTE = (
        "Covers frame period + this app's own processing + input dispatch, "
        "all measured live. It does NOT include sensor exposure/readout, "
        "USB transfer, or driver buffering upstream of OpenCV - those vary "
        "by camera and aren't visible to this process. Run the latency "
        "self-test for a true glass-to-detection number on this hardware."
    )


def estimate_pipeline(
    requested_fps: float,
    frame_interval: RollingStat,
    processing: RollingStat,
    dispatch: RollingStat,
) -> PipelineLatencyEstimate:
    """Combine live rolling stats into one estimate object for the UI."""
    frame_period = theoretical_frame_period_ms(requested_fps)

    achieved_fps = None
    jitter = 0.0
    if frame_interval.mean and frame_interval.mean > 0:
        achieved_fps = 1000.0 / frame_interval.mean
        jitter = frame_interval.stdev

    processing_ms = processing.mean
    dispatch_ms = dispatch.mean if not dispatch.is_empty else 0.0

    estimated_total = None
    if processing_ms is not None:
        estimated_total = frame_period + processing_ms + (dispatch_ms or 0.0)

    return PipelineLatencyEstimate(
        requested_fps=requested_fps,
        frame_period_ms=frame_period,
        achieved_fps=achieved_fps,
        frame_interval_jitter_ms=jitter,
        processing_ms=processing_ms,
        dispatch_ms=dispatch_ms,
        estimated_pipeline_ms=estimated_total,
        sample_count=len(processing),
    )


@dataclass
class SelfTestResult:
    samples_ms: list[float]

    @property
    def count(self) -> int:
        return len(self.samples_ms)

    @property
    def mean_ms(self) -> float | None:
        return statistics.fmean(self.samples_ms) if self.samples_ms else None

    @property
    def stdev_ms(self) -> float:
        return statistics.pstdev(self.samples_ms) if len(self.samples_ms) >= 2 else 0.0

    @property
    def min_ms(self) -> float | None:
        return min(self.samples_ms) if self.samples_ms else None

    @property
    def max_ms(self) -> float | None:
        return max(self.samples_ms) if self.samples_ms else None


class SelfTestRecorder:
    """Round-trip "flash a color, time until the detector sees it" test.

    This is the same idea used by longstanding webcam-latency test
    scripts: show something on screen at a known instant, point the
    camera at the screen, and measure the delay until the app itself
    reports having seen it. Unlike the live PipelineLatencyEstimate
    above, this number *does* include sensor, USB, and driver delay,
    because it's measured end-to-end rather than only inside this
    process - at the cost of needing the physical camera-at-monitor
    setup described in the UI.
    """

    def __init__(self) -> None:
        self._flash_time: float | None = None
        self._samples: list[float] = []

    def mark_flash(self) -> None:
        self._flash_time = time.perf_counter()

    def mark_detected(self) -> float | None:
        """Call when the detector first sees the flashed color. Returns the
        elapsed ms for this sample, or None if no flash is pending."""
        if self._flash_time is None:
            return None
        elapsed_ms = (time.perf_counter() - self._flash_time) * 1000.0
        self._samples.append(elapsed_ms)
        self._flash_time = None
        return elapsed_ms

    @property
    def awaiting_detection(self) -> bool:
        return self._flash_time is not None

    def reset_pending(self) -> None:
        """Clear a flash that never got detected (timeout), keeping past samples."""
        self._flash_time = None

    def result(self) -> SelfTestResult:
        return SelfTestResult(samples_ms=list(self._samples))

    def reset(self) -> None:
        self._flash_time = None
        self._samples.clear()

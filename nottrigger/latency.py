"""Rolling measurements for the live latency readout."""

from __future__ import annotations

import statistics
from collections import deque


def theoretical_frame_period_ms(fps: float) -> float:
    if fps <= 0:
        raise ValueError("fps must be positive")
    return 1000.0 / fps


class RollingStat:
    __slots__ = ("_values",)

    def __init__(self, maxlen: int = 90) -> None:
        self._values: deque[float] = deque(maxlen=maxlen)

    def add(self, value: float) -> None:
        self._values.append(value)

    def __len__(self) -> int:
        return len(self._values)

    @property
    def is_empty(self) -> bool:
        return not self._values

    @property
    def mean(self) -> float | None:
        if not self._values:
            return None
        return statistics.fmean(self._values)

    def reset(self) -> None:
        self._values.clear()


def estimate_pipeline(
    requested_fps: float,
    frame_interval: RollingStat,
    processing: RollingStat,
    dispatch: RollingStat,
) -> float | None:
    """Estimate frame interval plus recent processing and action time."""
    processing_ms = processing.mean
    if processing_ms is None:
        return None

    interval_ms = frame_interval.mean
    if interval_ms is None or interval_ms <= 0:
        interval_ms = theoretical_frame_period_ms(requested_fps)

    return interval_ms + processing_ms + (dispatch.mean or 0.0)
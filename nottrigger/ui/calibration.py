"""Latency self-test: flash the target color, time the detector's reaction.

Unlike the always-on pipeline estimate (frame period + measured
processing + dispatch), this number comes from an actual round trip
through the camera - sensor, USB, driver, and all - because it's timed
from when the color appeared on screen to when the running detector
first reported a match. The trade-off is it needs the physical setup
described in the window: point the camera at this swatch.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np

from nottrigger.camera import FrameResult
from nottrigger.config import TargetColor
from nottrigger.latency import SelfTestRecorder, SelfTestResult
from nottrigger.ui import theme
from nottrigger.ui.widgets import StatRow

NEUTRAL_HEX = "#808080"
FLASH_DELAY_MS = 1000
DETECTION_TIMEOUT_MS = 3000
INTER_TRIAL_PAUSE_MS = 600
DEFAULT_SAMPLE_COUNT = 10


def hsv_to_hex(target: TargetColor) -> str:
    bgr = cv2.cvtColor(np.uint8([[[target.h, target.s, target.v]]]), cv2.COLOR_HSV2BGR)[0][0]
    b, g, r = (int(c) for c in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


class CalibrationWindow(tk.Toplevel):
    def __init__(self, parent: tk.Widget, fonts: dict, target: TargetColor) -> None:
        super().__init__(parent)
        self.title("Latency self-test")
        self.configure(bg=theme.BG)
        self.geometry("520x460")
        self.minsize(420, 380)

        self._fonts = fonts
        self._target_hex = hsv_to_hex(target) if target.has_sample else "#3D6B5C"
        self._recorder = SelfTestRecorder()
        self._sample_target = DEFAULT_SAMPLE_COUNT
        self._state = "idle"  # idle -> waiting_flash -> waiting_detect -> done
        self._pending_after: str | None = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        pad = {"padx": 16, "pady": (12, 0)}

        ttk.Label(
            self,
            style="OnBg.TLabel",
            wraplength=470,
            justify="left",
            text=(
                "Point the camera at this window so the swatch below fills its "
                "region of interest. Each trial switches the swatch to your "
                "configured target color and times how long the running "
                "detector takes to report a match - a real measurement that "
                "includes camera, USB, and driver delay, not just this app's "
                "own processing."
            ),
        ).pack(fill="x", **pad)

        self._swatch = tk.Canvas(self, height=180, bg=NEUTRAL_HEX, highlightthickness=1, highlightbackground=theme.BORDER)
        self._swatch.pack(fill="x", padx=16, pady=12)

        controls = ttk.Frame(self, style="App.TFrame")
        controls.pack(fill="x", padx=16)
        self._start_btn = ttk.Button(controls, text=f"Run test ({self._sample_target} samples)", style="Accent.TButton", command=self._start)
        self._start_btn.pack(side="left")
        self._status_label = ttk.Label(controls, text="Idle", style="OnBg.TLabel")
        self._status_label.pack(side="left", padx=12)

        results = ttk.Frame(self, style="Panel.TFrame", padding=(14, 12))
        results.pack(fill="x", padx=16, pady=16)
        self._row_count = StatRow(results, "Samples", self._fonts)
        self._row_count.pack(fill="x", pady=2)
        self._row_mean = StatRow(results, "Mean glass-to-detect latency", self._fonts)
        self._row_mean.pack(fill="x", pady=2)
        self._row_spread = StatRow(results, "Spread (± 1 std dev)", self._fonts)
        self._row_spread.pack(fill="x", pady=2)
        self._row_range = StatRow(results, "Min / max", self._fonts)
        self._row_range.pack(fill="x", pady=2)

    # -- test sequence -----------------------------------------------------

    def _start(self) -> None:
        self._recorder.reset()
        self._start_btn.configure(state="disabled")
        self._update_results(self._recorder.result())
        self._next_trial()

    def _next_trial(self) -> None:
        if len(self._recorder.result().samples_ms) >= self._sample_target:
            self._finish()
            return
        self._state = "waiting_flash"
        self._set_status(f"Trial {len(self._recorder.result().samples_ms) + 1} of {self._sample_target}: get ready...")
        self._swatch.configure(bg=NEUTRAL_HEX)
        self._pending_after = self.after(FLASH_DELAY_MS, self._flash)

    def _flash(self) -> None:
        self._swatch.configure(bg=self._target_hex)
        self._recorder.mark_flash()
        self._state = "waiting_detect"
        self._set_status("Waiting for detection...")
        self._pending_after = self.after(DETECTION_TIMEOUT_MS, self._on_timeout)

    def _on_timeout(self) -> None:
        if self._state != "waiting_detect":
            return
        self._recorder.reset_pending()
        self._set_status("No match seen in time - check ROI/tolerance and try again.")
        self._state = "idle"
        self._start_btn.configure(state="normal")

    def observe(self, result: FrameResult) -> None:
        """Called by MainWindow's poll loop with every fresh FrameResult."""
        if self._state != "waiting_detect" or not result.is_match:
            return
        if self._pending_after is not None:
            self.after_cancel(self._pending_after)
            self._pending_after = None
        elapsed = self._recorder.mark_detected()
        self._update_results(self._recorder.result())
        if elapsed is not None:
            self._set_status(f"Detected after {elapsed:.0f} ms")
        self._swatch.configure(bg=NEUTRAL_HEX)
        self._state = "waiting_flash"
        self._pending_after = self.after(INTER_TRIAL_PAUSE_MS, self._next_trial)

    def _finish(self) -> None:
        self._state = "done"
        self._set_status("Done.")
        self._start_btn.configure(state="normal")
        self._swatch.configure(bg=NEUTRAL_HEX)

    def _set_status(self, text: str) -> None:
        self._status_label.configure(text=text)

    def _update_results(self, result: SelfTestResult) -> None:
        self._row_count.set(str(result.count))
        self._row_mean.set(f"{result.mean_ms:.0f} ms" if result.mean_ms is not None else "-")
        self._row_spread.set(f"{result.stdev_ms:.0f} ms" if result.count >= 2 else "-")
        if result.min_ms is not None:
            self._row_range.set(f"{result.min_ms:.0f} / {result.max_ms:.0f} ms")
        else:
            self._row_range.set("-")

    def _on_close(self) -> None:
        if self._pending_after is not None:
            self.after_cancel(self._pending_after)
        self.destroy()

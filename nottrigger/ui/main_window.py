"""The main application window."""

from __future__ import annotations

import logging
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from nottrigger.actions import ActionRecorder
from nottrigger.camera import CameraSettings, CameraWorker, FrameResult, list_cameras
from nottrigger.config import AppConfig, load_config, save_config
from nottrigger.constants import (
    CONFIG_SAVE_DEBOUNCE_MS,
    FPS_PRESETS,
    PREVIEW_MODE_LABELS,
    PREVIEW_MODES,
    RESOLUTION_PRESETS,
    STATS_WINDOW_DISPATCH,
    STATS_WINDOW_FRAME_INTERVAL,
    STATS_WINDOW_PROCESSING,
    UI_POLL_INTERVAL_MS,
)
from nottrigger.latency import ModeComparison, RollingStat, compare_modes, estimate_pipeline
from nottrigger.ui import theme
from nottrigger.ui.calibration import CalibrationWindow, hsv_to_hex
from nottrigger.ui.preview import PreviewCanvas
from nottrigger.ui.widgets import LabeledSlider, Section, StatRow, StatusPill, VScrollFrame, add_help

logger = logging.getLogger(__name__)

FIRED_DWELL_S = 0.5  # keep the "Fired" status visible this long so it's actually visible

# The three configurations the user most often wants compared, shown
# exactly as asked: 1080p30, 1080p60, and 4K30.
COMPARISON_MODES = [
    ("1080p @ 30 fps", 1920, 1080, 30),
    ("1080p @ 60 fps", 1920, 1080, 60),
    ("4K UHD @ 30 fps", 3840, 2160, 30),
]


class MainWindow:
    def __init__(self, root: tk.Tk, config_path: Path) -> None:
        self.root = root
        self.config_path = config_path
        self.config: AppConfig = load_config(config_path)

        self.fonts = theme.apply_theme(root)
        root.title("Not Triggerbot")
        root.geometry("1180x760")
        root.minsize(940, 620)
        root.configure(bg=theme.BG)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._worker: CameraWorker | None = None
        self._recorder: ActionRecorder | None = None
        self._calibration: CalibrationWindow | None = None
        self._save_after_id: str | None = None
        self._fired_until = 0.0

        self._frame_interval_stat = RollingStat(STATS_WINDOW_FRAME_INTERVAL)
        self._processing_stat = RollingStat(STATS_WINDOW_PROCESSING)
        self._dispatch_stat = RollingStat(STATS_WINDOW_DISPATCH)

        self._resolution_lookup = {label: (w, h) for label, w, h in RESOLUTION_PRESETS}

        self._build_layout()
        self._poll()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        header = ttk.Frame(self.root, style="App.TFrame", padding=(16, 12))
        header.pack(fill="x")
        ttk.Label(header, text="Not Triggerbot", style="Title.TLabel").pack(side="left")
        self._status_pill = StatusPill(header, self.fonts)
        self._status_pill.pack(side="left", padx=16)
        self._start_stop_btn = ttk.Button(header, text="Start", style="Accent.TButton", command=self._toggle_run)
        self._start_stop_btn.pack(side="right")

        body = ttk.Frame(self.root, style="App.TFrame")
        body.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        body.columnconfigure(0, weight=0, minsize=360)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = VScrollFrame(body, width=360)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left.grid_propagate(False)

        right = ttk.Frame(body, style="App.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        self._build_camera_section(left.body, 1)
        self._build_resolution_section(left.body, 2)
        self._build_adjustments_section(left.body, 3)
        self._build_roi_section(left.body, 4)
        self._build_color_section(left.body, 5)
        self._build_action_section(left.body, 6)
        self._build_performance_section(left.body, 7)

        self._preview = PreviewCanvas(right, on_roi_changed=self._on_roi_changed, on_color_sampled=self._on_color_sampled)
        self._preview.grid(row=0, column=0, sticky="nsew")
        self._preview.set_roi(self.config.roi)

        quick_stats = ttk.Frame(right, style="Panel.TFrame", padding=(12, 8))
        quick_stats.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        for i in range(4):
            quick_stats.columnconfigure(i, weight=1)
        self._qs_fps = self._quick_stat(quick_stats, 0, "Achieved fps")
        self._qs_processing = self._quick_stat(quick_stats, 1, "Processing")
        self._qs_dispatch = self._quick_stat(quick_stats, 2, "Dispatch")
        self._qs_match = self._quick_stat(quick_stats, 3, "Match")

    def _quick_stat(self, parent: tk.Widget, col: int, label: str) -> ttk.Label:
        cell = ttk.Frame(parent, style="Panel.TFrame")
        cell.grid(row=0, column=col, sticky="ew")
        ttk.Label(cell, text=label, style="Muted.TLabel").pack(anchor="w")
        value = ttk.Label(cell, text="-", style="StatValue.TLabel")
        value.pack(anchor="w")
        return value

    # ------------------------------------------------------------------
    # Section 1: Camera
    # ------------------------------------------------------------------

    def _build_camera_section(self, parent: tk.Widget, number: int) -> None:
        section = Section(parent, number, "Camera", self.fonts)
        section.pack(fill="x", pady=(0, 10))

        row = ttk.Frame(section.body, style="Panel.TFrame")
        row.pack(fill="x")
        self._camera_var = tk.StringVar()
        self._camera_combo = ttk.Combobox(row, textvariable=self._camera_var, state="readonly")
        self._camera_combo.pack(side="left", fill="x", expand=True)
        add_help(self._camera_combo, "Which USB camera to capture from. Names come from Windows if available.")

        refresh_btn = ttk.Button(row, text="Refresh", style="Ghost.TButton", command=self._refresh_cameras)
        refresh_btn.pack(side="left", padx=(8, 0))

        self._refresh_cameras(select_configured=True)

    def _refresh_cameras(self, select_configured: bool = False) -> None:
        devices = list_cameras()
        self._camera_devices = devices
        labels = [f"{d.index}: {d.name}" for d in devices] or ["No camera found"]
        self._camera_combo.configure(values=labels)
        if select_configured:
            match = next((label for label, d in zip(labels, devices) if d.index == self.config.camera_index), None)
            self._camera_var.set(match or (labels[0] if labels else ""))
        elif labels:
            self._camera_var.set(labels[0])
        self._on_camera_selected()

    def _on_camera_selected(self, *_args) -> None:
        idx = self._camera_combo.current()
        if idx < 0 or not getattr(self, "_camera_devices", None):
            return
        device = self._camera_devices[idx]
        self.config.camera_index = device.index
        self.config.camera_name = device.name
        self._schedule_save()

    # ------------------------------------------------------------------
    # Section 2: Resolution & frame rate
    # ------------------------------------------------------------------

    def _build_resolution_section(self, parent: tk.Widget, number: int) -> None:
        section = Section(parent, number, "Resolution & frame rate", self.fonts)
        section.pack(fill="x", pady=(0, 10))

        res_row = ttk.Frame(section.body, style="Panel.TFrame")
        res_row.pack(fill="x", pady=(0, 6))
        ttk.Label(res_row, text="Resolution", style="Body.TLabel").pack(side="left")
        self._res_var = tk.StringVar()
        res_combo = ttk.Combobox(
            res_row, textvariable=self._res_var, state="readonly",
            values=[label for label, _, _ in RESOLUTION_PRESETS],
        )
        current_res_label = next((l for l, w, h in RESOLUTION_PRESETS if w == self.config.width and h == self.config.height), RESOLUTION_PRESETS[2][0])
        self._res_var.set(current_res_label)
        res_combo.pack(side="right")

        fps_row = ttk.Frame(section.body, style="Panel.TFrame")
        fps_row.pack(fill="x", pady=(0, 6))
        ttk.Label(fps_row, text="Frame rate", style="Body.TLabel").pack(side="left")
        self._fps_var = tk.StringVar(value=str(self.config.fps) if self.config.fps in FPS_PRESETS else str(FPS_PRESETS[2]))
        fps_combo = ttk.Combobox(fps_row, textvariable=self._fps_var, state="readonly", values=[str(f) for f in FPS_PRESETS])
        fps_combo.pack(side="right")

        apply_btn = ttk.Button(section.body, text="Apply (restarts camera)", style="Ghost.TButton", command=self._apply_camera_settings)
        apply_btn.pack(fill="x", pady=(4, 10))
        add_help(apply_btn, "Resolution and frame rate need the camera stream reopened to take effect reliably, so they apply here rather than live.")

        ttk.Separator(section.body).pack(fill="x", pady=(0, 8))
        ttk.Label(section.body, text="How resolution and frame rate affect delay", style="Body.TLabel").pack(anchor="w")
        self._build_comparison_table(section.body)
        ttk.Label(
            section.body,
            style="Muted.TLabel",
            wraplength=310,
            justify="left",
            text=(
                "Frame period comes from fps alone, so 1080p30 and 4K30 have the "
                "same theoretical spacing between frames - resolution doesn't "
                "change that part. What resolution changes is how much data "
                "crosses USB per frame and how long this app's own processing "
                "takes. See Performance & latency below for what's actually "
                "happening on this camera, and to run a real measured test."
            ),
        ).pack(anchor="w", pady=(6, 0))

    def _build_comparison_table(self, parent: tk.Widget) -> None:
        table = ttk.Frame(parent, style="Panel.TFrame")
        table.pack(fill="x", pady=(6, 0))
        headers = ["Mode", "Frame period", "Megapixels"]
        for col, text in enumerate(headers):
            ttk.Label(table, text=text, style="Muted.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 10))
        rows: list[ModeComparison] = compare_modes(COMPARISON_MODES)
        for r, mode in enumerate(rows, start=1):
            ttk.Label(table, text=mode.label, style="Body.TLabel").grid(row=r, column=0, sticky="w", padx=(0, 10), pady=1)
            ttk.Label(table, text=f"{mode.frame_period_ms:.1f} ms", style="Body.TLabel").grid(row=r, column=1, sticky="w", padx=(0, 10))
            ttk.Label(table, text=f"{mode.megapixels:.2f} MP", style="Body.TLabel").grid(row=r, column=2, sticky="w")

    def _apply_camera_settings(self) -> None:
        label = self._res_var.get()
        if label in self._resolution_lookup:
            self.config.width, self.config.height = self._resolution_lookup[label]
        try:
            self.config.fps = int(self._fps_var.get())
        except ValueError:
            pass
        self._schedule_save()
        if self._worker is not None:
            self._restart_worker()

    # ------------------------------------------------------------------
    # Section 3: Image adjustments
    # ------------------------------------------------------------------

    def _build_adjustments_section(self, parent: tk.Widget, number: int) -> None:
        section = Section(parent, number, "Image adjustments", self.fonts)
        section.pack(fill="x", pady=(0, 10))
        add_help(
            section,
            "Applied live via the camera's own controls. Typical 0-255 ranges "
            "shown; some cameras use a different scale, so a slider that seems "
            "to do nothing or clips early just means this camera reports its "
            "range differently.",
        )

        self._adj_sliders: dict[str, LabeledSlider] = {}
        specs = [("brightness", "Brightness", 0, 255), ("contrast", "Contrast", 0, 255), ("saturation", "Saturation", 0, 255), ("exposure", "Exposure", -13, 0)]
        for key, label, lo, hi in specs:
            current = getattr(self.config, key)
            slider = LabeledSlider(
                section.body, label, lo, hi, current if current is not None else (lo + hi) / 2, self.fonts,
                on_change=lambda v, k=key: self._on_adjustment_changed(k, v),
            )
            slider.pack(fill="x", pady=4)
            self._adj_sliders[key] = slider

    def _on_adjustment_changed(self, key: str, value: float) -> None:
        setattr(self.config, key, value)
        self._schedule_save()
        if self._worker is not None:
            setattr(self._worker.settings, key, value)
            self._worker.apply_pending_adjustment(key, value)

    # ------------------------------------------------------------------
    # Section 4: Region of interest
    # ------------------------------------------------------------------

    def _build_roi_section(self, parent: tk.Widget, number: int) -> None:
        section = Section(parent, number, "Region of interest", self.fonts)
        section.pack(fill="x", pady=(0, 10))
        ttk.Label(
            section.body, style="Body.TLabel", wraplength=310, justify="left",
            text="Click and drag on the preview to draw the region the camera watches for a color mismatch.",
        ).pack(anchor="w")
        self._roi_readout = StatRow(section.body, "Current region", self.fonts)
        self._roi_readout.pack(fill="x", pady=(8, 0))
        self._update_roi_readout()
        clear_btn = ttk.Button(section.body, text="Clear region", style="Ghost.TButton", command=self._clear_roi)
        clear_btn.pack(anchor="w", pady=(8, 0))

    def _on_roi_changed(self, roi) -> None:
        self.config.roi = roi
        self._update_roi_readout()
        self._schedule_save()
        self._reset_detector_if_running()

    def _clear_roi(self) -> None:
        from nottrigger.config import RegionOfInterest

        self.config.roi = RegionOfInterest()
        self._preview.set_roi(self.config.roi)
        self._update_roi_readout()
        self._schedule_save()
        self._reset_detector_if_running()

    def _update_roi_readout(self) -> None:
        roi = self.config.roi
        text = "Not set" if roi.is_empty() else f"{roi.w} x {roi.h} px at ({roi.x}, {roi.y})"
        self._roi_readout.set(text)

    # ------------------------------------------------------------------
    # Section 5: Target color & sensitivity
    # ------------------------------------------------------------------

    def _build_color_section(self, parent: tk.Widget, number: int) -> None:
        section = Section(parent, number, "Target color & sensitivity", self.fonts)
        section.pack(fill="x", pady=(0, 10))

        swatch_row = ttk.Frame(section.body, style="Panel.TFrame")
        swatch_row.pack(fill="x")
        self._color_swatch = tk.Canvas(swatch_row, width=28, height=28, highlightthickness=1, highlightbackground=theme.BORDER)
        self._color_swatch.pack(side="left")
        self._color_label = ttk.Label(swatch_row, text="No color sampled", style="Body.TLabel")
        self._color_label.pack(side="left", padx=8)
        sample_btn = ttk.Button(swatch_row, text="Sample color", style="Ghost.TButton", command=self._start_color_sampling)
        sample_btn.pack(side="right")
        add_help(sample_btn, "Enable preview, then click this and click the color in the preview you want to detect.")
        self._refresh_color_swatch()

        for key, label in (("h_tolerance", "Hue tolerance"), ("s_tolerance", "Saturation tolerance"), ("v_tolerance", "Brightness tolerance")):
            slider = LabeledSlider(
                section.body, label, 0, 90 if key == "h_tolerance" else 255, getattr(self.config.target_color, key),
                self.fonts, on_change=lambda v, k=key: self._on_tolerance_changed(k, v),
            )
            slider.pack(fill="x", pady=4)

        self._threshold_slider = LabeledSlider(
            section.body, "Match threshold", 5, 100, self.config.match_threshold * 100, self.fonts,
            on_change=self._on_threshold_changed, value_format="{:.0f}%",
        )
        self._threshold_slider.pack(fill="x", pady=4)
        add_help(self._threshold_slider, "How much of the region must match the target color before it counts as a hit.")

        self._match_readout = StatRow(section.body, "Live match", self.fonts)
        self._match_readout.pack(fill="x", pady=(6, 0))

    def _start_color_sampling(self) -> None:
        if self.config.preview_mode == "off":
            self._color_label.configure(text="Enable preview first (Performance & latency below)")
            return
        self._preview.set_mode_sample_color()
        self._color_label.configure(text="Click the target color in the preview...")

    def _on_color_sampled(self, h: int, s: int, v: int) -> None:
        self.config.target_color.h, self.config.target_color.s, self.config.target_color.v = h, s, v
        self.config.target_color.has_sample = True
        self._refresh_color_swatch()
        self._schedule_save()
        self._reset_detector_if_running()

    def _refresh_color_swatch(self) -> None:
        tc = self.config.target_color
        if tc.has_sample:
            self._color_swatch.configure(bg=hsv_to_hex(tc))
            self._color_label.configure(text=f"H {tc.h}  S {tc.s}  V {tc.v}")
        else:
            self._color_swatch.configure(bg=theme.PANEL_BG)
            self._color_label.configure(text="No color sampled")

    def _on_tolerance_changed(self, key: str, value: float) -> None:
        setattr(self.config.target_color, key, int(value))
        self._schedule_save()
        self._reset_detector_if_running()

    def _on_threshold_changed(self, value: float) -> None:
        self.config.match_threshold = value / 100.0
        self._schedule_save()
        self._reset_detector_if_running()

    # ------------------------------------------------------------------
    # Section 6: Trigger action
    # ------------------------------------------------------------------

    def _build_action_section(self, parent: tk.Widget, number: int) -> None:
        section = Section(parent, number, "Trigger action", self.fonts)
        section.pack(fill="x", pady=(0, 10))

        row = ttk.Frame(section.body, style="Panel.TFrame")
        row.pack(fill="x")
        self._action_label = ttk.Label(row, text=self.config.action.label, style="Body.TLabel")
        self._action_label.pack(side="left")
        self._record_btn = ttk.Button(row, text="Record action", style="Ghost.TButton", command=self._start_action_recording)
        self._record_btn.pack(side="right")
        add_help(self._record_btn, "Click this, then press the key or mouse button the QC software expects.")

        self._confirm_slider = LabeledSlider(
            section.body, "Confirmation frames", 1, 15, self.config.confirm_frames, self.fonts,
            on_change=self._on_confirm_frames_changed,
        )
        self._confirm_slider.pack(fill="x", pady=4)
        add_help(self._confirm_slider, "How many frames in a row must match before firing, to ignore single-frame noise.")

        self._cooldown_slider = LabeledSlider(
            section.body, "Cooldown (ms)", 0, 3000, self.config.cooldown_ms, self.fonts, value_format="{:.0f} ms",
            on_change=self._on_cooldown_changed,
        )
        self._cooldown_slider.pack(fill="x", pady=4)
        add_help(self._cooldown_slider, "Minimum time between fires, as a second guard against rapid re-triggering.")

    def _start_action_recording(self) -> None:
        self._record_btn.configure(state="disabled")
        self._action_label.configure(text="Press a key or click a mouse button...")
        self._recorder = ActionRecorder()
        self._recorder.start(on_captured=lambda action: self.root.after(0, self._on_action_recorded, action))

    def _on_action_recorded(self, action) -> None:
        self.config.action = action
        self._action_label.configure(text=action.label)
        self._record_btn.configure(state="normal")
        self._schedule_save()

    def _on_confirm_frames_changed(self, value: float) -> None:
        self.config.confirm_frames = int(value)
        self._schedule_save()
        self._reset_detector_if_running()

    def _on_cooldown_changed(self, value: float) -> None:
        self.config.cooldown_ms = int(value)
        self._schedule_save()

    # ------------------------------------------------------------------
    # Section 7: Performance & latency
    # ------------------------------------------------------------------

    def _build_performance_section(self, parent: tk.Widget, number: int) -> None:
        section = Section(parent, number, "Performance & latency", self.fonts)
        section.pack(fill="x", pady=(0, 10))

        preview_row = ttk.Frame(section.body, style="Panel.TFrame")
        preview_row.pack(fill="x")
        ttk.Label(preview_row, text="Preview", style="Body.TLabel").pack(side="left")
        self._preview_var = tk.StringVar(value=PREVIEW_MODE_LABELS[self.config.preview_mode])
        preview_combo = ttk.Combobox(
            preview_row, textvariable=self._preview_var, state="readonly",
            values=[PREVIEW_MODE_LABELS[m] for m in PREVIEW_MODES],
        )
        preview_combo.pack(side="right")
        preview_combo.bind("<<ComboboxSelected>>", self._on_preview_mode_changed)
        add_help(
            preview_row,
            "Off skips all preview rendering for the lowest possible overhead; "
            "detection keeps running at full speed either way. Cheap throttles "
            "and shrinks the preview image; Full redraws every frame.",
        )

        self._pipeline_note_shown = False
        for key, label in (
            ("achieved_fps", "Achieved fps"),
            ("processing_ms", "Detection processing"),
            ("dispatch_ms", "Action dispatch"),
            ("estimated_pipeline_ms", "Estimated pipeline latency"),
        ):
            row = StatRow(section.body, label, self.fonts)
            row.pack(fill="x", pady=2)
            setattr(self, f"_perf_{key}", row)

        ttk.Label(
            section.body, style="Muted.TLabel", wraplength=310, justify="left",
            text="This estimate covers frame period plus this app's own measured processing and dispatch time. It excludes sensor, USB, and driver delay upstream of this process - those vary by camera and aren't visible from here.",
        ).pack(anchor="w", pady=(4, 8))

        calibrate_btn = ttk.Button(section.body, text="Run latency self-test", style="Ghost.TButton", command=self._open_calibration)
        calibrate_btn.pack(fill="x")
        add_help(calibrate_btn, "Point the camera at a flashing on-screen swatch to measure real end-to-end latency, including camera and USB delay.")

    def _on_preview_mode_changed(self, *_args) -> None:
        label_to_mode = {v: k for k, v in PREVIEW_MODE_LABELS.items()}
        mode = label_to_mode.get(self._preview_var.get(), "cheap")
        self.config.preview_mode = mode
        if self._worker is not None:
            self._worker.set_preview_mode(mode)
        if mode == "off":
            self._preview.show_disabled()
        self._schedule_save()

    def _open_calibration(self) -> None:
        if self._calibration is not None and self._calibration.winfo_exists():
            self._calibration.lift()
            return
        self._calibration = CalibrationWindow(self.root, self.fonts, self.config.target_color)

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------

    def _toggle_run(self) -> None:
        if self._worker is None:
            self._start_worker()
        else:
            self._stop_worker()

    def _start_worker(self) -> None:
        settings = CameraSettings(
            index=self.config.camera_index,
            width=self.config.width,
            height=self.config.height,
            fps=self.config.fps,
            brightness=self.config.brightness,
            contrast=self.config.contrast,
            saturation=self.config.saturation,
            exposure=self.config.exposure,
            buffer_size=self.config.buffer_size,
        )
        worker = CameraWorker(
            settings=settings,
            roi_provider=lambda: self.config.roi,
            target_provider=lambda: self.config.target_color,
            detection_params_provider=lambda: (self.config.match_threshold, self.config.confirm_frames, self.config.cooldown_ms),
            action_provider=lambda: self.config.action,
            on_error=lambda msg: self.root.after(0, self._handle_camera_error, msg),
        )
        worker.set_preview_mode(self.config.preview_mode)
        worker.start()
        self._worker = worker
        self._start_stop_btn.configure(text="Stop")
        self._frame_interval_stat.reset()
        self._processing_stat.reset()
        self._dispatch_stat.reset()

    def _stop_worker(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.join(timeout=2.0)
            self._worker = None
        self._start_stop_btn.configure(text="Start")
        self._status_pill.set_status("idle", "Idle")

    def _restart_worker(self) -> None:
        self._stop_worker()
        self._start_worker()

    def _handle_camera_error(self, message: str) -> None:
        self._worker = None
        self._start_stop_btn.configure(text="Start")
        self._status_pill.set_status("idle", "Idle")
        messagebox.showerror("Camera error", message)

    def _reset_detector_if_running(self) -> None:
        if self._worker is not None:
            self._worker.reset_detector()

    # ------------------------------------------------------------------
    # Poll loop: the only place camera-thread results touch Tk widgets
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        if self._worker is not None:
            result = self._worker.results.pop()
            if result is not None:
                self._apply_frame_result(result)
        self.root.after(UI_POLL_INTERVAL_MS, self._poll)

    def _apply_frame_result(self, result: FrameResult) -> None:
        if result.frame_interval_ms is not None:
            self._frame_interval_stat.add(result.frame_interval_ms)
        self._processing_stat.add(result.processing_ms)
        if result.dispatch_ms is not None:
            self._dispatch_stat.add(result.dispatch_ms)

        if result.should_fire:
            self._fired_until = time.perf_counter() + FIRED_DWELL_S

        now = time.perf_counter()
        if now < self._fired_until:
            self._status_pill.set_status("fired", "Fired")
            self._preview.set_status_color("fired")
        elif result.is_match:
            self._status_pill.set_status("match", "Match")
            self._preview.set_status_color("match")
        else:
            self._status_pill.set_status("armed", "Armed")
            self._preview.set_status_color("armed")

        self._match_readout.set(f"{result.match_ratio * 100:.0f}%  (need {self.config.match_threshold * 100:.0f}%)")

        if result.preview_rgb is not None:
            self._preview.show_frame(result.preview_rgb, result.preview_scale)

        self._update_performance_panel()

        self._qs_match.configure(text=f"{result.match_ratio * 100:.0f}%")

    def _update_performance_panel(self) -> None:
        requested_fps = self._worker.actual_fps if self._worker and self._worker.actual_fps else float(self.config.fps)
        estimate = estimate_pipeline(requested_fps, self._frame_interval_stat, self._processing_stat, self._dispatch_stat)

        fps_text = f"{estimate.achieved_fps:.1f}" if estimate.achieved_fps else "Measuring..."
        self._perf_achieved_fps.set(fps_text)
        self._qs_fps.configure(text=fps_text)

        proc_text = f"{estimate.processing_ms:.2f} ms" if estimate.processing_ms is not None else "-"
        self._perf_processing_ms.set(proc_text)
        self._qs_processing.configure(text=proc_text)

        dispatch_text = f"{estimate.dispatch_ms:.2f} ms" if estimate.dispatch_ms else "-"
        self._perf_dispatch_ms.set(dispatch_text)
        self._qs_dispatch.configure(text=dispatch_text)

        total_text = f"{estimate.estimated_pipeline_ms:.1f} ms" if estimate.estimated_pipeline_ms is not None else "Measuring..."
        self._perf_estimated_pipeline_ms.set(total_text)

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def _schedule_save(self) -> None:
        if self._save_after_id is not None:
            self.root.after_cancel(self._save_after_id)
        self._save_after_id = self.root.after(CONFIG_SAVE_DEBOUNCE_MS, self._save_now)

    def _save_now(self) -> None:
        self._save_after_id = None
        save_config(self.config_path, self.config)

    def _on_close(self) -> None:
        if self._save_after_id is not None:
            self.root.after_cancel(self._save_after_id)
        self._stop_worker()
        save_config(self.config_path, self.config)
        self.root.destroy()

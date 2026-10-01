"""The main application window."""

from __future__ import annotations

import colorsys
import logging
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from nottrigger.actions import ActionRecorder
from nottrigger.camera import CameraSettings, CameraWorker, FrameResult, list_cameras
from nottrigger.config import AppConfig, RegionOfInterest, TargetColor, load_config, save_config
from nottrigger.constants import (
    CONFIG_SAVE_DEBOUNCE_MS,
    STATS_WINDOW_DISPATCH,
    STATS_WINDOW_FRAME_INTERVAL,
    STATS_WINDOW_PROCESSING,
    UI_POLL_INTERVAL_MS,
)
from nottrigger.latency import RollingStat, estimate_pipeline
from nottrigger.ui import theme
from nottrigger.ui.preview import PreviewCanvas
from nottrigger.ui.widgets import LabeledSlider, Section, StatRow, StatusPill, VScrollFrame, add_help

logger = logging.getLogger(__name__)

FIRED_DWELL_S = 0.5  # keep the "Fired" status visible this long so it's actually visible


def hsv_to_hex(target_color: TargetColor) -> str:
    red, green, blue = colorsys.hsv_to_rgb(
        target_color.h / 180.0,
        target_color.s / 255.0,
        target_color.v / 255.0,
    )
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


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
        self._save_after_id: str | None = None
        self._fired_until = 0.0
        self._show_preview = True
        self._low_cpu = False

        self._frame_interval_stat = RollingStat(STATS_WINDOW_FRAME_INTERVAL)
        self._processing_stat = RollingStat(STATS_WINDOW_PROCESSING)
        self._dispatch_stat = RollingStat(STATS_WINDOW_DISPATCH)

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
        self._latency_var = tk.StringVar(value="Latency --")
        ttk.Label(header, textvariable=self._latency_var, style="OnBg.TLabel").pack(side="left", padx=8)
        self._preview_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            header, text="Preview", variable=self._preview_var, command=self._on_preview_options_changed
        ).pack(side="right", padx=(8, 0))
        self._low_cpu_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            header, text="Low CPU preview", variable=self._low_cpu_var, command=self._on_preview_options_changed
        ).pack(side="right", padx=(8, 0))
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
        self._build_capture_section(left.body, 2)
        self._build_adjustments_section(left.body, 3)
        self._build_roi_section(left.body, 4)
        self._build_color_section(left.body, 5)
        self._build_action_section(left.body, 6)

        self._preview = PreviewCanvas(right, on_roi_changed=self._on_roi_changed, on_color_sampled=self._on_color_sampled)
        self._preview.grid(row=0, column=0, sticky="nsew")
        self._preview.set_roi(self.config.roi)

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
        self._camera_combo.bind("<<ComboboxSelected>>", self._on_camera_selected)
        add_help(self._camera_combo, "Which USB camera to capture from. Names come from Windows if available.")

        refresh_btn = ttk.Button(row, text="Refresh", style="Ghost.TButton", command=self._refresh_cameras)
        refresh_btn.pack(side="left", padx=(8, 0))

        self._camera_mode_label = ttk.Label(section.body, text="Camera mode: Native", style="Muted.TLabel")
        self._camera_mode_label.pack(anchor="w", pady=(6, 0))
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
        changed = device.index != self.config.camera_index
        self.config.camera_index = device.index
        self.config.camera_name = device.name
        if changed:
            self.config.requested_fps = None
            self._fps_var.set("") if hasattr(self, "_fps_var") else None
        self._schedule_save()
        if changed and self._worker is not None:
            self._restart_worker()

    # ------------------------------------------------------------------
    # Section 2: Capture settings
    # ------------------------------------------------------------------

    def _build_capture_section(self, parent: tk.Widget, number: int) -> None:
        section = Section(parent, number, "Capture settings", self.fonts)
        section.pack(fill="x", pady=(0, 10))

        row = ttk.Frame(section.body, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="FPS override", style="Body.TLabel").pack(side="left")
        self._fps_var = tk.StringVar(
            value="" if self.config.requested_fps is None else str(self.config.requested_fps)
        )
        ttk.Spinbox(row, from_=1, to=240, increment=1, width=8, textvariable=self._fps_var).pack(side="right")
        ttk.Label(
            section.body,
            text="Leave blank to use the camera's default frame rate. Resolution stays at the camera default.",
            style="Muted.TLabel",
            wraplength=310,
            justify="left",
        ).pack(anchor="w", pady=(5, 0))

        apply_btn = ttk.Button(section.body, text="Apply frame rate", style="Ghost.TButton", command=self._apply_camera_settings)
        apply_btn.pack(fill="x", pady=(4, 10))
        add_help(apply_btn, "Changing the frame rate restarts the camera. Leave it blank to return to the camera's default.")

    def _apply_camera_settings(self) -> None:
        value = self._fps_var.get().strip()
        try:
            self.config.requested_fps = int(value) if value else None
            if self.config.requested_fps is not None and not 1 <= self.config.requested_fps <= 240:
                raise ValueError
        except ValueError:
            messagebox.showerror("Invalid frame rate", "Enter a frame rate from 1 to 240, or leave it blank.")
            return
        self._schedule_save()
        if self._worker is not None:
            self._restart_worker()

    # ------------------------------------------------------------------
    # Section 3: Image adjustments
    # ------------------------------------------------------------------

    def _build_adjustments_section(self, parent: tk.Widget, number: int) -> None:
        section = Section(parent, number, "Brightness & exposure", self.fonts)
        section.pack(fill="x", pady=(0, 10))
        add_help(
            section,
            "Applied live via the camera's own controls. Typical 0-255 ranges "
            "shown; some cameras use a different scale, so a slider that seems "
            "to do nothing or clips early just means this camera reports its "
            "range differently.",
        )

        self._adj_sliders: dict[str, LabeledSlider] = {}
        specs = [("brightness", "Brightness", 0, 255), ("exposure", "Exposure", -13, 0)]
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
            text="Choose a shape. Click for a point or circle, or drag to draw a rectangle.",
        ).pack(anchor="w")
        shape_row = ttk.Frame(section.body, style="Panel.TFrame")
        shape_row.pack(fill="x", pady=(6, 0))
        ttk.Label(shape_row, text="Shape", style="Body.TLabel").pack(side="left")
        if self.config.roi.shape not in ("rectangle", "circle", "point"):
            self.config.roi.shape = "rectangle"
        self._roi_shape_var = tk.StringVar(value=self.config.roi.shape.title())
        self._roi_shape_combo = ttk.Combobox(
            shape_row,
            textvariable=self._roi_shape_var,
            state="readonly",
            values=("Rectangle", "Circle", "Point"),
            width=12,
        )
        self._roi_shape_combo.pack(side="right")
        self._roi_shape_combo.bind("<<ComboboxSelected>>", self._on_roi_shape_changed)
        self._roi_radius_slider = LabeledSlider(
            section.body,
            "Circle radius",
            1,
            300,
            self.config.roi.radius,
            self.fonts,
            value_format="{:.0f} px",
            on_change=self._on_roi_radius_changed,
        )
        if self.config.roi.shape == "circle":
            self._roi_radius_slider.pack(fill="x", pady=4)
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

    def _on_roi_shape_changed(self, *_args) -> None:
        shape = self._roi_shape_var.get().lower()
        old_shape = self.config.roi.shape
        roi = self.config.roi
        if shape in ("point", "circle") and old_shape == "rectangle" and not roi.is_empty():
            roi.x += roi.w // 2
            roi.y += roi.h // 2
            roi.w = roi.h = 1
        elif shape == "rectangle" and old_shape == "circle" and not roi.is_empty():
            roi.x -= roi.radius
            roi.y -= roi.radius
            roi.w = roi.h = roi.radius * 2 + 1
        self.config.roi.shape = shape
        self._preview.set_roi_shape(shape, self.config.roi.radius)
        if shape == "circle":
            self._roi_radius_slider.pack(fill="x", pady=4)
        else:
            self._roi_radius_slider.pack_forget()
        self._update_roi_readout()
        self._schedule_save()
        self._reset_detector_if_running()

    def _on_roi_radius_changed(self, value: float) -> None:
        self.config.roi.radius = int(value)
        self._preview.set_roi(self.config.roi)
        self._update_roi_readout()
        self._schedule_save()
        self._reset_detector_if_running()

    def _clear_roi(self) -> None:
        self.config.roi = RegionOfInterest(
            shape=self._roi_shape_var.get().lower(),
            radius=self.config.roi.radius,
        )
        self._preview.set_roi(self.config.roi)
        self._update_roi_readout()
        self._schedule_save()
        self._reset_detector_if_running()

    def _update_roi_readout(self) -> None:
        roi = self.config.roi
        if roi.is_empty():
            text = "Not set"
        elif roi.shape == "point":
            text = f"1 pixel at ({roi.x}, {roi.y})"
        elif roi.shape == "circle":
            text = f"Circle, radius {roi.radius} px at ({roi.x}, {roi.y})"
        else:
            text = f"{roi.w} x {roi.h} px at ({roi.x}, {roi.y})"
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
        add_help(sample_btn, "Start the camera, click this, then select the color in the preview.")
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
        if not self._show_preview:
            self._color_label.configure(text="Turn on Preview to sample a color")
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
            requested_fps=self.config.requested_fps,
            brightness=self.config.brightness,
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
        worker.set_preview_options(self._show_preview, self._low_cpu)
        worker.start()
        self._worker = worker
        self._start_stop_btn.configure(text="Stop")
        self._frame_interval_stat.reset()
        self._processing_stat.reset()
        self._dispatch_stat.reset()
        self._preview.set_latency(None)

    def _stop_worker(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker.join(timeout=2.0)
            self._worker = None
        self._start_stop_btn.configure(text="Start")
        self._status_pill.set_status("idle", "Idle")
        self._latency_var.set("Latency --")
        self._preview.set_latency(None)

    def _restart_worker(self) -> None:
        self._stop_worker()
        self._start_worker()

    def _handle_camera_error(self, message: str) -> None:
        self._worker = None
        self._start_stop_btn.configure(text="Start")
        self._status_pill.set_status("idle", "Idle")
        self._latency_var.set("Latency --")
        self._preview.set_latency(None)
        messagebox.showerror("Camera error", message)

    def _reset_detector_if_running(self) -> None:
        if self._worker is not None:
            self._worker.reset_detector()

    # ------------------------------------------------------------------
    # Poll loop: the only place camera-thread results touch Tk widgets
    # ------------------------------------------------------------------

    def _poll(self) -> None:
        if self._worker is not None:
            if self._worker.actual_width and self._worker.actual_height:
                fps_text = f", {self._worker.actual_fps:.0f} fps" if self._worker.actual_fps else ""
                self._camera_mode_label.configure(
                    text=f"Camera mode: {self._worker.actual_width} x {self._worker.actual_height}{fps_text}"
                )
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

        self._update_latency_readout()

    def _update_latency_readout(self) -> None:
        requested_fps = self._worker.actual_fps if self._worker and self._worker.actual_fps else 30.0
        latency = estimate_pipeline(
            requested_fps,
            self._frame_interval_stat,
            self._processing_stat,
            self._dispatch_stat,
        )
        text = f"Latency ~{latency:.0f} ms" if latency is not None else "Latency measuring..."
        self._latency_var.set(text)
        self._preview.set_latency(text if self._show_preview else None)

    def _on_preview_options_changed(self) -> None:
        self._show_preview = self._preview_var.get()
        self._low_cpu = self._low_cpu_var.get()
        self._preview.set_preview_visible(self._show_preview)
        self._preview.set_latency(self._latency_var.get() if self._show_preview else None)
        if self._worker is not None:
            self._worker.set_preview_options(self._show_preview, self._low_cpu)

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
        if self._recorder is not None:
            self._recorder.stop()
        self._stop_worker()
        save_config(self.config_path, self.config)
        self.root.destroy()

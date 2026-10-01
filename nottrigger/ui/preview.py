"""Live preview canvas: image rendering, ROI drawing, color sampling.

Three coordinate spaces are in play here, and getting the conversions
right matters - it's the classic bug in tools like this (ROI drawn on a
scaled-down preview ends up reading the wrong pixels at full resolution):

  1. Native frame space - the camera's actual captured resolution.
     Detection and the stored ROI in config both live here.
  2. Preview array space - the (possibly downscaled) RGB copy the
     camera thread handed off, per its `preview_scale`.
  3. Canvas space - where the image is actually drawn, after fitting
     the preview array into whatever size the widget currently is.

`_total_scale` composes (2) and (3) so canvas <-> native conversions are
always one multiply/divide, not two.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

import cv2
import numpy as np

from nottrigger.config import RegionOfInterest
from nottrigger.ui import theme

MIN_ROI_SIZE = 6  # native pixels; ignore accidental single-point clicks


class PreviewCanvas(tk.Canvas):
    def __init__(
        self,
        parent: tk.Widget,
        on_roi_changed: Callable[[RegionOfInterest], None],
        on_color_sampled: Callable[[int, int, int], None],
        **kwargs,
    ) -> None:
        super().__init__(parent, bg="#15170F", highlightthickness=0, **kwargs)
        self._on_roi_changed = on_roi_changed
        self._on_color_sampled = on_color_sampled

        self._photo = None  # keep a reference; Tk drops images with none
        self._image_item = None
        self._roi_item = None
        self._drag_item = None
        self._placeholder_item = None

        self._preview_array: np.ndarray | None = None  # last RGB array shown
        self._preview_scale = 1.0  # native -> preview array
        self._fit_scale = 1.0  # preview array -> canvas
        self._offset = (0, 0)  # canvas-space top-left of the drawn image

        self._roi_native = RegionOfInterest()
        self._roi_shape = "rectangle"
        self._roi_radius = 20
        self._preview_visible = True
        self._mode = "roi"  # "roi" or "sample"
        self._drag_start_canvas: tuple[int, int] | None = None

        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._show_placeholder("Waiting for camera...")
        self._latency_bg_item = self.create_rectangle(0, 0, 1, 1, fill="#22261F", outline="", state="hidden")
        self._latency_item = self.create_text(
            0, 0, text="", anchor="ne", fill="#FFFFFF", font=("Segoe UI", 10, "bold"), state="hidden"
        )

    # -- public API ------------------------------------------------------

    def set_mode_sample_color(self) -> None:
        self._mode = "sample"

    def set_roi(self, roi: RegionOfInterest) -> None:
        self._roi_native = roi
        self._roi_shape = roi.shape
        self._roi_radius = roi.radius
        self._redraw_roi()

    def set_roi_shape(self, shape: str, radius: int) -> None:
        self._roi_shape = shape
        self._roi_radius = radius
        self._redraw_roi()

    def set_status_color(self, status: str) -> None:
        color = theme.status_color(status)
        if self._roi_item is not None:
            self.itemconfigure(self._roi_item, outline=color)

    def show_frame(self, rgb: np.ndarray, preview_scale: float) -> None:
        if not self._preview_visible:
            return
        self._hide_placeholder()
        self._preview_array = rgb
        self._preview_scale = preview_scale if preview_scale > 0 else 1.0
        self._render()

    def set_latency(self, text: str | None) -> None:
        if text is None:
            self.itemconfigure(self._latency_bg_item, state="hidden")
            self.itemconfigure(self._latency_item, state="hidden")
            return
        self.itemconfigure(self._latency_item, text=text, state="normal")
        self.itemconfigure(self._latency_bg_item, state="normal")
        self._position_latency()

    def set_preview_visible(self, visible: bool) -> None:
        self._preview_visible = visible
        if not visible:
            self._preview_array = None
            if self._image_item is not None:
                self.itemconfigure(self._image_item, state="hidden")
            if self._roi_item is not None:
                self.itemconfigure(self._roi_item, state="hidden")
            self._show_placeholder("Preview hidden. Detection is still running.")
            self.set_latency(None)
        else:
            self._show_placeholder("Waiting for camera...")
            self._redraw_roi()

    # -- rendering ---------------------------------------------------------

    def _render(self) -> None:
        if self._preview_array is None:
            return
        # Import here: ImageTk needs a Tk root to exist, which it does by
        # the time this runs, but importing at module load can be finicky
        # in some headless/test contexts.
        from PIL import Image, ImageTk

        canvas_w = max(self.winfo_width(), 1)
        canvas_h = max(self.winfo_height(), 1)
        img_h, img_w = self._preview_array.shape[:2]

        self._fit_scale = min(canvas_w / img_w, canvas_h / img_h)
        disp_w = max(1, round(img_w * self._fit_scale))
        disp_h = max(1, round(img_h * self._fit_scale))
        self._offset = ((canvas_w - disp_w) // 2, (canvas_h - disp_h) // 2)

        array = self._preview_array
        if (disp_w, disp_h) != (img_w, img_h):
            array = cv2.resize(array, (disp_w, disp_h), interpolation=cv2.INTER_LINEAR)

        image = Image.fromarray(array)
        self._photo = ImageTk.PhotoImage(image)

        if self._image_item is None:
            self._image_item = self.create_image(*self._offset, anchor="nw", image=self._photo)
        else:
            self.coords(self._image_item, *self._offset)
            self.itemconfigure(self._image_item, image=self._photo, state="normal")
        self.tag_lower(self._image_item)

        self._redraw_roi()
        self._position_latency()

    def _position_latency(self) -> None:
        width = max(self.winfo_width(), 1)
        self.coords(self._latency_item, width - 12, 12)
        bounds = self.bbox(self._latency_item)
        if bounds is not None:
            self.coords(
                self._latency_bg_item,
                bounds[0] - 10,
                bounds[1] - 6,
                bounds[2] + 10,
                bounds[3] + 6,
            )
        self.tag_raise(self._latency_bg_item)
        self.tag_raise(self._latency_item)

    def _show_placeholder(self, text: str) -> None:
        if self._placeholder_item is None:
            self._placeholder_item = self.create_text(
                10, 10, text=text, anchor="nw", fill="#C9CCC4", font=("Segoe UI", 10)
            )
        else:
            self.itemconfigure(self._placeholder_item, text=text)
            self.coords(self._placeholder_item, 10, 10)

    def _hide_placeholder(self) -> None:
        if self._placeholder_item is not None:
            self.delete(self._placeholder_item)
            self._placeholder_item = None

    def _redraw_roi(self) -> None:
        if self._roi_native.is_empty() or self._preview_array is None or not self._preview_visible:
            if self._roi_item is not None:
                self.itemconfigure(self._roi_item, state="hidden")
            return
        if self._roi_shape == "point":
            x0, y0 = self._native_to_canvas(self._roi_native.x, self._roi_native.y)
            coords = (x0 - 4, y0 - 4, x0 + 4, y0 + 4)
            create = self.create_oval
        elif self._roi_shape == "circle":
            x0, y0 = self._native_to_canvas(
                self._roi_native.x - self._roi_radius,
                self._roi_native.y - self._roi_radius,
            )
            x1, y1 = self._native_to_canvas(
                self._roi_native.x + self._roi_radius,
                self._roi_native.y + self._roi_radius,
            )
            coords = (x0, y0, x1, y1)
            create = self.create_oval
        else:
            x0, y0 = self._native_to_canvas(self._roi_native.x, self._roi_native.y)
            x1, y1 = self._native_to_canvas(
                self._roi_native.x + self._roi_native.w,
                self._roi_native.y + self._roi_native.h,
            )
            coords = (x0, y0, x1, y1)
            create = self.create_rectangle
        if self._roi_item is None:
            self._roi_item = create(*coords, outline=theme.status_color("idle"), width=2)
        else:
            self.coords(self._roi_item, *coords)
        self.itemconfigure(self._roi_item, state="normal" if self._preview_visible else "hidden")

    # -- coordinate transforms --------------------------------------------

    @property
    def _total_scale(self) -> float:
        return self._preview_scale * self._fit_scale if self._fit_scale else 1.0

    def _canvas_to_native(self, cx: float, cy: float) -> tuple[int, int]:
        ox, oy = self._offset
        scale = self._total_scale or 1.0
        return int((cx - ox) / scale), int((cy - oy) / scale)

    def _native_to_canvas(self, nx: float, ny: float) -> tuple[int, int]:
        ox, oy = self._offset
        scale = self._total_scale or 1.0
        return int(nx * scale + ox), int(ny * scale + oy)

    # -- mouse handling ----------------------------------------------------

    def _on_press(self, event: tk.Event) -> None:
        if self._mode == "sample":
            self._handle_sample_click(event)
            self._mode = "roi"
            return
        if self._preview_array is None:
            return
        if self._roi_shape in ("point", "circle"):
            x, y = self._canvas_to_native(event.x, event.y)
            height, width = self._preview_array.shape[:2]
            width = max(1, round(width / self._preview_scale))
            height = max(1, round(height / self._preview_scale))
            x = min(max(0, x), width - 1)
            y = min(max(0, y), height - 1)
            self._roi_native = RegionOfInterest(
                x=x,
                y=y,
                w=1,
                h=1,
                shape=self._roi_shape,
                radius=self._roi_radius,
            )
            self._redraw_roi()
            self._on_roi_changed(self._roi_native)
            return
        self._drag_start_canvas = (event.x, event.y)
        if self._drag_item is not None:
            self.delete(self._drag_item)
        self._drag_item = self.create_rectangle(
            event.x, event.y, event.x, event.y, outline=theme.ACCENT, width=2, dash=(4, 2)
        )

    def _on_drag(self, event: tk.Event) -> None:
        if self._drag_start_canvas is None or self._drag_item is None:
            return
        x0, y0 = self._drag_start_canvas
        self.coords(self._drag_item, x0, y0, event.x, event.y)

    def _on_release(self, event: tk.Event) -> None:
        if self._drag_start_canvas is None:
            return
        x0c, y0c = self._drag_start_canvas
        self._drag_start_canvas = None
        if self._drag_item is not None:
            self.delete(self._drag_item)
            self._drag_item = None

        nx0, ny0 = self._canvas_to_native(min(x0c, event.x), min(y0c, event.y))
        nx1, ny1 = self._canvas_to_native(max(x0c, event.x), max(y0c, event.y))
        w, h = nx1 - nx0, ny1 - ny0
        if w < MIN_ROI_SIZE or h < MIN_ROI_SIZE:
            return  # treat as a stray click, not a real ROI drag

        roi = RegionOfInterest(x=max(0, nx0), y=max(0, ny0), w=w, h=h, shape="rectangle")
        self._roi_native = roi
        self._redraw_roi()
        self._on_roi_changed(roi)

    def _handle_sample_click(self, event: tk.Event) -> None:
        if self._preview_array is None:
            return
        nx, ny = self._canvas_to_native(event.x, event.y)
        px = int(nx * self._preview_scale)
        py = int(ny * self._preview_scale)
        arr = self._preview_array
        h, w = arr.shape[:2]
        px = min(max(px, 0), w - 1)
        py = min(max(py, 0), h - 1)
        r, g, b = (int(c) for c in arr[py, px])
        hsv_pixel = cv2.cvtColor(np.uint8([[[r, g, b]]]), cv2.COLOR_RGB2HSV)[0][0]
        self._on_color_sampled(int(hsv_pixel[0]), int(hsv_pixel[1]), int(hsv_pixel[2]))

"""Small reusable widgets shared across the settings panels."""

from __future__ import annotations

import tkinter as tk
import math
from tkinter import ttk
from typing import Callable

from nottrigger.ui import theme


def normalize_numeric_entry(
    value: str,
    lower: float,
    upper: float,
    resolution: float = 1.0,
) -> float | None:
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number) or not math.isfinite(resolution) or resolution <= 0:
        return None
    number = min(upper, max(lower, number))
    digits = max(0, len(f"{resolution:.10f}".rstrip("0").split(".")[-1]))
    return round(lower + round((number - lower) / resolution) * resolution, digits)


class ToolTip:
    """Plain-English help text on hover, for every setting in the app.

    One ToolTip per widget; shows after a short delay so it doesn't
    flash while the mouse is just passing through, and follows the
    widget rather than the cursor so it doesn't jitter.
    """

    _DELAY_MS = 450

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self._after_id: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self._DELAY_MS, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8

        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self._tip,
            text=self.text,
            justify="left",
            background="#FFFDE7",
            foreground=theme.TEXT,
            relief="solid",
            borderwidth=1,
            wraplength=260,
            padx=8,
            pady=6,
            font=("Segoe UI", 9),
        )
        label.pack()

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def add_help(widget: tk.Widget, text: str) -> ToolTip:
    return ToolTip(widget, text)


class Section(ttk.Frame):
    """A numbered, titled panel - the building block of the 1-7 setup flow."""

    def __init__(self, parent: tk.Widget, number: int, title: str, fonts: dict, **kwargs) -> None:
        super().__init__(parent, style="Panel.TFrame", **kwargs)
        self.configure(padding=(14, 12))
        # A thin manual border, since ttk.Frame has no borderwidth/relief
        # that reliably renders under the "clam" theme.
        self._border = tk.Frame(self, bg=theme.BORDER)

        header = ttk.Frame(self, style="Panel.TFrame")
        header.pack(fill="x", anchor="w")

        badge = tk.Canvas(header, width=22, height=22, highlightthickness=0, bg=theme.PANEL_BG)
        badge.create_oval(1, 1, 21, 21, fill=theme.ACCENT, outline="")
        badge.create_text(11, 11, text=str(number), fill=theme.ACCENT_TEXT, font=("Segoe UI", 9, "bold"))
        badge.pack(side="left", padx=(0, 8))

        ttk.Label(header, text=title, style="Section.TLabel").pack(side="left", anchor="w")

        self.body = ttk.Frame(self, style="Panel.TFrame")
        self.body.pack(fill="both", expand=True, pady=(10, 0))


class LabeledSlider(ttk.Frame):
    """A slider with a live value label; commits (debounced) on change.

    `on_change` fires on every drag tick with the live value so callers
    can update a readout immediately, but callers doing anything
    heavier (like a config save) should debounce themselves - see
    MainWindow's config-save scheduling.
    """

    def __init__(
        self,
        parent: tk.Widget,
        label: str,
        from_: float,
        to: float,
        value: float,
        fonts: dict,
        on_change: Callable[[float], None] | None = None,
        value_format: str = "{:.0f}",
        resolution: float = 1.0,
        **kwargs,
    ) -> None:
        super().__init__(parent, style="Panel.TFrame", **kwargs)
        self._on_change = on_change
        self._lower = float(from_)
        self._upper = float(to)
        self._resolution = resolution

        top = ttk.Frame(self, style="Panel.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text=label, style="Body.TLabel").pack(side="left")
        self._entry_var = tk.StringVar(value=self._entry_text(value))
        unit = value_format.partition("}")[2].strip()
        if unit:
            ttk.Label(top, text=unit, style="Muted.TLabel").pack(side="right", padx=(0, 5))
        self._entry = ttk.Entry(top, textvariable=self._entry_var, width=8, justify="right")
        self._entry.pack(side="right")
        self._entry.bind("<Return>", self._commit_entry)
        self._entry.bind("<FocusOut>", self._commit_entry)

        self._var = tk.DoubleVar(value=value)
        self._scale = ttk.Scale(
            self, from_=from_, to=to, variable=self._var, orient="horizontal", command=self._handle_change
        )
        self._scale.pack(fill="x", pady=(2, 0))

    def _handle_change(self, _value: str) -> None:
        value = normalize_numeric_entry(
            self._entry_text(self._var.get()), self._lower, self._upper, self._resolution
        )
        if value is None:
            return
        self._var.set(value)
        self._entry_var.set(self._entry_text(value))
        if self._on_change:
            self._on_change(value)

    def _commit_entry(self, _event=None) -> None:
        value = normalize_numeric_entry(
            self._entry_var.get(), self._lower, self._upper, self._resolution
        )
        if value is None:
            value = self._var.get()
        self._var.set(value)
        self._entry_var.set(self._entry_text(value))
        if self._on_change:
            self._on_change(value)

    @staticmethod
    def _entry_text(value: float) -> str:
        return f"{value:g}"

    def get(self) -> float:
        return self._var.get()

    def set(self, value: float) -> None:
        value = normalize_numeric_entry(
            self._entry_text(value), self._lower, self._upper, self._resolution
        )
        if value is None:
            return
        self._var.set(value)
        self._entry_var.set(self._entry_text(value))


class StatRow(ttk.Frame):
    """A quiet "label ................ value" readout row."""

    def __init__(self, parent: tk.Widget, label: str, fonts: dict, **kwargs) -> None:
        super().__init__(parent, style="Panel.TFrame", **kwargs)
        ttk.Label(self, text=label, style="Muted.TLabel").pack(side="left")
        self._value = ttk.Label(self, text="-", style="Body.TLabel")
        self._value.pack(side="right")

    def set(self, text: str) -> None:
        self._value.configure(text=text)


class VScrollFrame(ttk.Frame):
    """A vertically scrollable frame. Put widgets inside `.body`."""

    def __init__(self, parent: tk.Widget, **kwargs) -> None:
        super().__init__(parent, style="App.TFrame", **kwargs)
        canvas = tk.Canvas(self, bg=theme.BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.body = ttk.Frame(canvas, style="App.TFrame")
        self._window = canvas.create_window((0, 0), window=self.body, anchor="nw")
        self._canvas = canvas

        self.body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", self._on_canvas_resize)
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    def _on_canvas_resize(self, event: tk.Event) -> None:
        self._canvas.itemconfigure(self._window, width=event.width)

    def _on_mousewheel(self, event: tk.Event) -> None:
        self._canvas.yview_scroll(int(-event.delta / 120), "units")


class StatusPill(tk.Canvas):
    """A small colored status indicator with a text label."""

    def __init__(self, parent: tk.Widget, fonts: dict, width: int = 130, height: int = 28, **kwargs) -> None:
        super().__init__(parent, width=width, height=height, highlightthickness=0, bg=theme.BG, **kwargs)
        self._dot = self.create_oval(4, height // 2 - 6, 16, height // 2 + 6, fill=theme.status_color("idle"), outline="")
        self._label = self.create_text(
            24, height // 2, text="Idle", anchor="w", font=fonts.get("body", ("Segoe UI", 10)), fill=theme.TEXT
        )

    def set_status(self, status: str, text: str) -> None:
        self.itemconfigure(self._dot, fill=theme.status_color(status))
        self.itemconfigure(self._label, text=text)

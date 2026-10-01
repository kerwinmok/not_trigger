"""Visual theme: one small palette, one type scale, applied once at startup.

Design intent: this runs next to a conveyor belt for hours, glanced at
rather than "browsed," so it's built like a control panel - flat,
high-contrast, quiet - rather than a marketing page. Color is used
functionally (status = green/amber/red) and nowhere else; there is
exactly one accent color and it's spent on the Start/Stop control and
active-section numbering, not scattered around.
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

# --- Palette -----------------------------------------------------------
BG = "#EEF0ED"  # window background
PANEL_BG = "#FFFFFF"  # section panels, preview frame
BORDER = "#D7DAD5"  # hairline dividers/borders
TEXT = "#22261F"  # primary text
TEXT_MUTED = "#6C7066"  # help text, secondary labels
ACCENT = "#3D6B5C"  # the one accent: Start/Stop, section numerals, links
ACCENT_TEXT = "#FFFFFF"

STATUS_IDLE = "#8A8F84"
STATUS_ARMED = "#B8862E"
STATUS_MATCH = "#3D7A4F"
STATUS_FIRED = "#B23B2E"

# --- Type scale ----------------------------------------------------------
FONT_FAMILY = "Segoe UI"  # falls back automatically if unavailable
FONT_FALLBACKS = ("Segoe UI", "Helvetica", "Arial", "TkDefaultFont")


def _resolve_family() -> str:
    available = set(tkfont.families())
    for name in FONT_FALLBACKS:
        if name in available:
            return name
    return "TkDefaultFont"


def apply_theme(root: tk.Tk) -> dict[str, tkfont.Font]:
    """Configure ttk styles and return the named fonts the UI code uses."""
    family = _resolve_family()

    fonts = {
        "title": tkfont.Font(family=family, size=13, weight="bold"),
        "section": tkfont.Font(family=family, size=11, weight="bold"),
        "body": tkfont.Font(family=family, size=10),
        "help": tkfont.Font(family=family, size=9),
        "mono": tkfont.Font(family="Consolas", size=10),
        "stat_value": tkfont.Font(family=family, size=14, weight="bold"),
    }

    root.configure(bg=BG)

    style = ttk.Style(root)
    # "clam" renders flat and respects custom colors consistently across
    # platforms; the default Windows theme ignores a lot of style options.
    style.theme_use("clam")

    style.configure("App.TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL_BG, relief="flat")
    style.configure(
        "Panel.TLabelframe",
        background=PANEL_BG,
        bordercolor=BORDER,
        relief="solid",
        borderwidth=1,
    )
    style.configure("Panel.TLabelframe.Label", background=PANEL_BG, foreground=TEXT)

    style.configure("Body.TLabel", background=PANEL_BG, foreground=TEXT, font=fonts["body"])
    style.configure("Muted.TLabel", background=PANEL_BG, foreground=TEXT_MUTED, font=fonts["help"])
    style.configure("Section.TLabel", background=PANEL_BG, foreground=TEXT, font=fonts["section"])
    style.configure(
        "StatValue.TLabel", background=PANEL_BG, foreground=TEXT, font=fonts["stat_value"]
    )
    style.configure("OnBg.TLabel", background=BG, foreground=TEXT, font=fonts["body"])
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=fonts["title"])

    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground=ACCENT_TEXT,
        font=fonts["section"],
        padding=(14, 8),
        borderwidth=0,
    )
    style.map("Accent.TButton", background=[("active", "#345A4D"), ("disabled", "#A8B3AC")])

    style.configure("Ghost.TButton", padding=(10, 6), font=fonts["body"])

    style.configure("TScale", background=PANEL_BG)
    style.configure("TCombobox", padding=4)

    return fonts


def status_color(status: str) -> str:
    return {
        "idle": STATUS_IDLE,
        "armed": STATUS_ARMED,
        "match": STATUS_MATCH,
        "fired": STATUS_FIRED,
    }.get(status, STATUS_IDLE)

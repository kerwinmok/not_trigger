"""Shared constants for Not Triggerbot.

Kept dependency-free (no cv2 / tkinter imports) so any module can import
this without pulling in the rest of the app.
"""

from __future__ import annotations

APP_NAME = "Not Triggerbot"
APP_VERSION = "2.0.0"

CONFIG_FILENAME = "config.json"
LOG_FILENAME = "not_trigger.log"

# (label, width, height). Order matters: shown top-to-bottom in the UI.
RESOLUTION_PRESETS: list[tuple[str, int, int]] = [
    ("480p (640x480)", 640, 480),
    ("720p (1280x720)", 1280, 720),
    ("1080p (1920x1080)", 1920, 1080),
    ("4K UHD (3840x2160)", 3840, 2160),
]

FPS_PRESETS: list[int] = [15, 24, 30, 60]

# Keep preview work small regardless of camera resolution and frame rate.
CHEAP_PREVIEW_FPS = 12
CHEAP_PREVIEW_MAX_WIDTH = 480

# UI polls the camera worker for new stats/preview frames on this cadence,
# independent of camera fps.
UI_POLL_INTERVAL_MS = 40  # 25 Hz UI refresh ceiling

# How long to wait after the last settings change before writing
# config.json, so dragging a slider doesn't hit disk on every tick.
CONFIG_SAVE_DEBOUNCE_MS = 500

# Rolling-window sizes for live latency stats (in samples, i.e. frames).
STATS_WINDOW_FRAME_INTERVAL = 90
STATS_WINDOW_PROCESSING = 90
STATS_WINDOW_DISPATCH = 30

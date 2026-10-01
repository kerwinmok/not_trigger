"""Application bootstrap."""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from pathlib import Path

from nottrigger.constants import CONFIG_FILENAME
from nottrigger.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def _app_dir() -> Path:
    """Directory to read/write config.json and the log file.

    Next to the running script (or the frozen executable, if this is
    ever packaged with PyInstaller/similar) rather than the current
    working directory, so launching via a shortcut from anywhere still
    finds the same config.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def run() -> None:
    app_dir = _app_dir()
    setup_logging(app_dir)
    logger.info("Starting Not Triggerbot from %s", app_dir)

    from nottrigger.ui.main_window import MainWindow  # deferred: needs Tk available

    root = tk.Tk()
    MainWindow(root, app_dir / CONFIG_FILENAME)
    root.mainloop()

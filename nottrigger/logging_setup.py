"""Logging setup.

A GUI app running unattended next to a conveyor belt that just vanishes
on a crash is hard to debug after the fact. This sets up a small
rotating log file alongside config.json, and installs a hook so an
uncaught exception gets logged (not just dumped to a console window
that may not even be visible) before the process exits.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from nottrigger.constants import LOG_FILENAME


def setup_logging(app_dir: Path) -> None:
    log_path = app_dir / LOG_FILENAME
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(console)

    def log_uncaught(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("uncaught").critical(
            "Unhandled exception", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = log_uncaught

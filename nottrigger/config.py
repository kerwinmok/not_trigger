"""Application configuration: a plain dataclass plus JSON load/save.

Design goals for this refactor:
  - A config.json from an older version, or one a user hand-edited and
    broke, should never crash the app on startup. Unknown keys are
    ignored, missing keys fall back to defaults, and bad values fall
    back to defaults field-by-field rather than aborting the whole load.
  - Everything the UI can change lives here, so "current settings" has
    one source of truth instead of being scattered across widgets.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from nottrigger.constants import DEFAULT_PREVIEW_MODE

logger = logging.getLogger(__name__)


@dataclass
class TargetColor:
    """An HSV color to match against, plus per-channel tolerance.

    Hue is OpenCV-style 0-179 (circular). Saturation/value are 0-255.
    """

    h: int = 0
    s: int = 0
    v: int = 0
    h_tolerance: int = 12
    s_tolerance: int = 60
    v_tolerance: int = 60
    has_sample: bool = False  # False until the user has actually sampled a color


@dataclass
class RegionOfInterest:
    """Pixel rect in the *native capture frame's* coordinate space.

    Kept in native pixels (not preview/display pixels) so it stays
    correct no matter what preview scaling or window size is active.
    """

    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0

    def is_empty(self) -> bool:
        return self.w <= 0 or self.h <= 0


@dataclass
class TriggerAction:
    """A recorded key or mouse action to fire on detection."""

    kind: str = ""  # "" (unset), "key", or "mouse"
    value: str = ""  # character, special-key name, or mouse button name
    is_special_key: bool = False
    label: str = "Not set"


@dataclass
class AppConfig:
    # Camera selection
    camera_index: int = 0
    camera_name: str = ""

    # Resolution / frame rate
    width: int = 1920
    height: int = 1080
    fps: int = 30

    # Image adjustments (None = leave at camera/driver default)
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    exposure: float | None = None

    # Detection
    roi: RegionOfInterest = field(default_factory=RegionOfInterest)
    target_color: TargetColor = field(default_factory=TargetColor)
    match_threshold: float = 0.35  # fraction of ROI pixels that must match
    confirm_frames: int = 3
    cooldown_ms: int = 400

    # Action
    action: TriggerAction = field(default_factory=TriggerAction)

    # Performance / preview
    preview_mode: str = DEFAULT_PREVIEW_MODE
    buffer_size: int = 1

    # Window
    window_geometry: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "AppConfig":
        """Build a config from a (possibly partial or stale) dict.

        Unknown top-level keys are dropped. Missing keys use the
        dataclass default. Nested dataclasses (roi, target_color,
        action) are merged the same way, one level deep.
        """
        cfg = AppConfig()
        if not isinstance(data, dict):
            return cfg

        valid_keys = {f.name for f in fields(AppConfig)}
        nested_types = {
            "roi": RegionOfInterest,
            "target_color": TargetColor,
            "action": TriggerAction,
        }

        for key, value in data.items():
            if key not in valid_keys:
                continue
            if key in nested_types:
                nested = _safe_nested(nested_types[key], value)
                if nested is not None:
                    setattr(cfg, key, nested)
            else:
                default_val = getattr(cfg, key)
                setattr(cfg, key, _coerce(value, default_val))
        return cfg


def _safe_nested(cls: type, value: Any):
    """Build a nested dataclass from a dict, defaulting any bad/missing field."""
    if not isinstance(value, dict):
        return None
    instance = cls()
    for f in fields(cls):
        if f.name in value:
            try:
                setattr(instance, f.name, _coerce(value[f.name], getattr(instance, f.name)))
            except (TypeError, ValueError):
                pass  # keep the default for this one field
    return instance


def _coerce(value: Any, default: Any) -> Any:
    """Best-effort coercion of a loaded JSON value to match the default's type.

    JSON has no int/float distinction issues for our purposes, but a
    hand-edited config.json can easily have a string where a number
    belongs, or vice versa. Rather than raise, fall back to the default.
    """
    if default is None:
        # Optional numeric field (brightness/contrast/...): accept a
        # number or None, reject anything else.
        if value is None or isinstance(value, (int, float)):
            return value
        return default
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return value


def load_config(path: Path) -> AppConfig:
    """Load config.json, falling back to defaults on any problem."""
    if not path.exists():
        logger.info("No config file at %s, starting with defaults.", path)
        return AppConfig()
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read config at %s (%s); using defaults.", path, exc)
        return AppConfig()
    return AppConfig.from_dict(raw)


def save_config(path: Path, config: AppConfig) -> None:
    """Write config.json atomically (write to a temp file, then replace).

    Avoids leaving a half-written, corrupt config.json if the process is
    killed mid-write (e.g. the PC loses power on the warehouse floor).
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            json.dump(config.to_dict(), fh, indent=2)
        tmp_path.replace(path)
    except OSError as exc:
        logger.error("Could not save config to %s: %s", path, exc)

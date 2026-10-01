"""Recording and firing the trigger action (a single key or mouse click).

pynput is imported lazily inside the functions/methods that need it,
rather than at module load time. That keeps `import nottrigger.actions`
safe on a machine that doesn't have pynput installed (useful for running
the rest of the test suite on a non-Windows dev box); the real app only
ever runs where pynput is present.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from nottrigger.config import TriggerAction

MOUSE_BUTTONS = ("left", "right", "middle")


def _describe_key(key) -> tuple[str, bool, str]:
    """pynput Key/KeyCode -> (value, is_special, human label)."""
    char = getattr(key, "char", None)
    if char:
        return char, False, f"Key: {char}"
    name = getattr(key, "name", str(key))
    return name, True, f"Key: {name}"


def key_to_action(key) -> TriggerAction:
    value, is_special, label = _describe_key(key)
    return TriggerAction(kind="key", value=value, is_special_key=is_special, label=label)


def mouse_button_to_action(button) -> TriggerAction:
    name = getattr(button, "name", str(button))
    return TriggerAction(kind="mouse", value=name, is_special_key=False, label=f"Mouse: {name} click")


@dataclass
class ActionRecorder:
    """Captures the next key press or mouse click and reports it once."""

    _keyboard_listener: object = None
    _mouse_listener: object = None

    def start(self, on_captured: Callable[[TriggerAction], None]) -> None:
        from pynput import keyboard, mouse

        def finish(action: TriggerAction) -> None:
            self.stop()
            on_captured(action)

        def on_press(key):
            finish(key_to_action(key))
            return False  # stop this listener

        def on_click(x, y, button, pressed):
            if pressed:
                finish(mouse_button_to_action(button))
                return False
            return True

        self._keyboard_listener = keyboard.Listener(on_press=on_press)
        self._mouse_listener = mouse.Listener(on_click=on_click)
        self._keyboard_listener.start()
        self._mouse_listener.start()

    def stop(self) -> None:
        if self._keyboard_listener is not None:
            self._keyboard_listener.stop()
            self._keyboard_listener = None
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
            self._mouse_listener = None


class ActionDispatcher:
    """Fires a previously-recorded TriggerAction."""

    def __init__(self) -> None:
        self._keyboard_controller = None
        self._mouse_controller = None

    def _ensure_controllers(self) -> None:
        if self._keyboard_controller is None:
            from pynput.keyboard import Controller as KeyboardController

            self._keyboard_controller = KeyboardController()
        if self._mouse_controller is None:
            from pynput.mouse import Controller as MouseController

            self._mouse_controller = MouseController()

    def fire(self, action: TriggerAction) -> float:
        """Perform the action. Returns the dispatch time in milliseconds."""
        t0 = time.perf_counter()
        if not action.kind:
            return 0.0

        self._ensure_controllers()
        if action.kind == "key":
            key_obj = self._resolve_key(action)
            self._keyboard_controller.press(key_obj)
            self._keyboard_controller.release(key_obj)
        elif action.kind == "mouse":
            from pynput.mouse import Button

            button = getattr(Button, action.value, Button.left)
            self._mouse_controller.click(button, 1)

        return (time.perf_counter() - t0) * 1000.0

    @staticmethod
    def _resolve_key(action: TriggerAction):
        from pynput.keyboard import Key, KeyCode

        if action.is_special_key:
            return getattr(Key, action.value)
        return KeyCode.from_char(action.value)

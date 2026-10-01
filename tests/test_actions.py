import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

from nottrigger.actions import ActionDispatcher, ActionRecorder, mouse_button_to_action


def test_recorded_left_button_dispatches_as_lmb(monkeypatch):
    buttons = SimpleNamespace(left=object(), right=object(), middle=object())
    mouse_module = ModuleType("pynput.mouse")
    mouse_module.Button = buttons
    monkeypatch.setitem(sys.modules, "pynput.mouse", mouse_module)

    action = mouse_button_to_action(SimpleNamespace(name="left"))
    dispatcher = ActionDispatcher()
    dispatcher._ensure_controllers = lambda: None
    dispatcher._mouse_controller = Mock()

    dispatcher.fire(action)

    assert action.value == "left"
    assert action.label == "Mouse: LMB"
    dispatcher._mouse_controller.click.assert_called_once_with(buttons.left, 1)


def test_mouse_aliases_map_to_supported_buttons():
    assert mouse_button_to_action(SimpleNamespace(name="lmb")).value == "left"
    assert mouse_button_to_action(SimpleNamespace(name="rmb")).value == "right"
    assert mouse_button_to_action(SimpleNamespace(name="mmb")).value == "middle"


def test_recorder_only_delivers_first_captured_action():
    recorder = ActionRecorder()
    captured = []
    action = mouse_button_to_action(SimpleNamespace(name="left"))

    recorder._finish(action, captured.append)
    recorder._finish(action, captured.append)

    assert captured == [action]
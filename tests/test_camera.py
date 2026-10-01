from unittest.mock import Mock

import numpy as np

from nottrigger import camera
from nottrigger.camera import CameraSettings, CameraWorker
from nottrigger.config import RegionOfInterest, TargetColor, TriggerAction


def _worker(settings=None):
    return CameraWorker(
        settings=settings or CameraSettings(),
        roi_provider=RegionOfInterest,
        target_provider=TargetColor,
        detection_params_provider=lambda: (0.35, 3, 400),
        action_provider=TriggerAction,
    )


def test_native_capture_does_not_override_camera_format_or_fps(monkeypatch):
    capture = Mock()
    capture.isOpened.return_value = True
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda *_args: capture)

    _worker()._open_capture()

    properties = {call.args[0] for call in capture.set.call_args_list}
    assert camera.cv2.CAP_PROP_FRAME_WIDTH not in properties
    assert camera.cv2.CAP_PROP_FRAME_HEIGHT not in properties
    assert camera.cv2.CAP_PROP_FPS not in properties
    assert camera.cv2.CAP_PROP_FOURCC not in properties


def test_requested_fps_is_the_only_optional_capture_override(monkeypatch):
    capture = Mock()
    capture.isOpened.return_value = True
    monkeypatch.setattr(camera.cv2, "VideoCapture", lambda *_args: capture)

    _worker(CameraSettings(requested_fps=60))._open_capture()

    capture.set.assert_any_call(camera.cv2.CAP_PROP_FPS, 60)
    assert camera.cv2.CAP_PROP_FRAME_WIDTH not in [call.args[0] for call in capture.set.call_args_list]


def test_hidden_preview_skips_preview_conversion():
    worker = _worker()
    worker.set_preview_options(enabled=False, low_cpu=False)

    image, scale = worker._maybe_build_preview(np.zeros((480, 640, 3), dtype=np.uint8), 1.0)

    assert image is None
    assert scale == 1.0


def test_low_cpu_preview_uses_small_slow_preview():
    worker = _worker()
    worker.set_preview_options(enabled=True, low_cpu=True)

    image, scale = worker._maybe_build_preview(np.zeros((480, 640, 3), dtype=np.uint8), 1.0)

    assert image.shape == (180, 240, 3)
    assert scale == 240 / 640
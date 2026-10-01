import json

from nottrigger.config import AppConfig, load_config, save_config


def test_round_trip(tmp_path):
    path = tmp_path / "config.json"
    cfg = AppConfig()
    cfg.requested_fps = 60
    cfg.roi.x, cfg.roi.y, cfg.roi.w, cfg.roi.h = 10, 20, 100, 80
    cfg.roi.shape = "circle"
    cfg.roi.radius = 42
    cfg.target_color.h, cfg.target_color.has_sample = 5, True
    save_config(path, cfg)

    loaded = load_config(path)
    assert loaded.requested_fps == 60
    assert loaded.roi.x == 10 and loaded.roi.w == 100
    assert loaded.roi.shape == "circle" and loaded.roi.radius == 42
    assert loaded.target_color.h == 5
    assert loaded.target_color.has_sample is True


def test_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "does_not_exist.json")
    assert cfg.requested_fps is None


def test_corrupt_json_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not valid json", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.requested_fps is None


def test_partial_and_unknown_fields_do_not_crash(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"fps": 60, "width": 1280, "some_future_field": "x", "roi": {"x": 5}}),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.requested_fps is None
    assert cfg.roi.x == 5
    assert cfg.roi.y == 0  # untouched field keeps its default


def test_bad_value_type_falls_back_to_default_for_that_field(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"requested_fps": "not a number"}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.requested_fps is None


def test_invalid_fps_and_roi_values_are_normalized(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"requested_fps": 500, "roi": {"shape": "triangle", "radius": 900}}),
        encoding="utf-8",
    )
    cfg = load_config(path)

    assert cfg.requested_fps is None
    assert cfg.roi.shape == "rectangle"
    assert cfg.roi.radius == 300


def test_save_is_atomic_no_stray_tmp_file(tmp_path):
    path = tmp_path / "config.json"
    save_config(path, AppConfig())
    assert path.exists()
    assert not (tmp_path / "config.json.tmp").exists()

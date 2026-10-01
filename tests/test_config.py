import json

from nottrigger.config import AppConfig, load_config, save_config


def test_round_trip(tmp_path):
    path = tmp_path / "config.json"
    cfg = AppConfig()
    cfg.width, cfg.height, cfg.fps = 3840, 2160, 30
    cfg.roi.x, cfg.roi.y, cfg.roi.w, cfg.roi.h = 10, 20, 100, 80
    cfg.target_color.h, cfg.target_color.has_sample = 5, True
    save_config(path, cfg)

    loaded = load_config(path)
    assert loaded.width == 3840 and loaded.height == 2160
    assert loaded.roi.x == 10 and loaded.roi.w == 100
    assert loaded.target_color.h == 5
    assert loaded.target_color.has_sample is True


def test_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "does_not_exist.json")
    assert cfg.width == AppConfig().width


def test_corrupt_json_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not valid json", encoding="utf-8")
    cfg = load_config(path)
    assert cfg.fps == AppConfig().fps


def test_partial_and_unknown_fields_do_not_crash(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"fps": 60, "some_future_field": "x", "roi": {"x": 5}}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.fps == 60
    assert cfg.roi.x == 5
    assert cfg.roi.y == 0  # untouched field keeps its default


def test_bad_value_type_falls_back_to_default_for_that_field(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"fps": "not a number", "width": 1920}), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.width == 1920
    assert cfg.fps == AppConfig().fps  # bad value, fell back instead of crashing


def test_save_is_atomic_no_stray_tmp_file(tmp_path):
    path = tmp_path / "config.json"
    save_config(path, AppConfig())
    assert path.exists()
    assert not (tmp_path / "config.json.tmp").exists()

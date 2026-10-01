import pytest

from nottrigger.latency import (
    RollingStat,
    SelfTestRecorder,
    compare_modes,
    estimate_pipeline,
    megapixels,
    theoretical_frame_period_ms,
)


def test_frame_period_depends_only_on_fps():
    # 1080p30 and 4K30 must have the identical theoretical frame period -
    # this is the crux of the "resolution doesn't change frame period"
    # point made in the UI and README.
    assert theoretical_frame_period_ms(30) == pytest.approx(33.333, rel=1e-3)
    assert theoretical_frame_period_ms(30) == theoretical_frame_period_ms(30)


def test_frame_period_60fps_is_half_of_30fps():
    assert theoretical_frame_period_ms(60) == pytest.approx(theoretical_frame_period_ms(30) / 2, rel=1e-6)


def test_4k_is_exactly_4x_the_pixels_of_1080p():
    assert megapixels(3840, 2160) == pytest.approx(4 * megapixels(1920, 1080), rel=1e-9)


def test_compare_modes_matches_expected_readme_figures():
    rows = compare_modes(
        [
            ("1080p30", 1920, 1080, 30),
            ("1080p60", 1920, 1080, 60),
            ("4K30", 3840, 2160, 30),
        ]
    )
    by_label = {r.label: r for r in rows}
    assert by_label["1080p30"].frame_period_ms == pytest.approx(33.33, abs=0.01)
    assert by_label["1080p60"].frame_period_ms == pytest.approx(16.67, abs=0.01)
    assert by_label["4K30"].frame_period_ms == pytest.approx(33.33, abs=0.01)  # same as 1080p30
    assert by_label["1080p30"].megapixels == pytest.approx(2.0736, abs=0.001)
    assert by_label["4K30"].megapixels == pytest.approx(8.2944, abs=0.001)


def test_rolling_stat_mean_and_stdev():
    stat = RollingStat(maxlen=5)
    for v in [10, 10, 10, 10, 10]:
        stat.add(v)
    assert stat.mean == pytest.approx(10.0)
    assert stat.stdev == pytest.approx(0.0)

    stat2 = RollingStat(maxlen=5)
    for v in [1, 2, 3, 4, 5]:
        stat2.add(v)
    assert stat2.mean == pytest.approx(3.0)
    assert stat2.stdev > 0


def test_rolling_stat_window_drops_old_values():
    stat = RollingStat(maxlen=3)
    for v in [1, 1, 1, 100]:
        stat.add(v)
    assert len(stat) == 3
    assert stat.mean == pytest.approx((1 + 1 + 100) / 3)


def test_estimate_pipeline_empty_is_not_ready():
    estimate = estimate_pipeline(30, RollingStat(), RollingStat(), RollingStat())
    assert estimate.is_ready is False
    assert estimate.frame_period_ms == pytest.approx(33.33, abs=0.01)


def test_estimate_pipeline_sums_components():
    frame_interval = RollingStat()
    processing = RollingStat()
    dispatch = RollingStat()
    for _ in range(5):
        frame_interval.add(33.33)
        processing.add(2.0)
        dispatch.add(0.5)

    estimate = estimate_pipeline(30, frame_interval, processing, dispatch)
    assert estimate.is_ready
    assert estimate.estimated_pipeline_ms == pytest.approx(33.33 + 2.0 + 0.5, abs=0.01)
    assert estimate.achieved_fps == pytest.approx(30.0, abs=0.1)


def test_self_test_recorder_round_trip():
    recorder = SelfTestRecorder()
    assert recorder.awaiting_detection is False
    recorder.mark_flash()
    assert recorder.awaiting_detection is True
    elapsed = recorder.mark_detected()
    assert elapsed is not None and elapsed >= 0
    assert recorder.awaiting_detection is False
    assert recorder.result().count == 1


def test_self_test_recorder_mark_detected_without_flash_is_noop():
    recorder = SelfTestRecorder()
    assert recorder.mark_detected() is None
    assert recorder.result().count == 0

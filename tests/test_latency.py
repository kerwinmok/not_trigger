import pytest

from nottrigger.latency import RollingStat, estimate_pipeline, theoretical_frame_period_ms


def test_frame_period_uses_requested_fps():
    assert theoretical_frame_period_ms(30) == pytest.approx(33.333, rel=1e-3)
    with pytest.raises(ValueError):
        theoretical_frame_period_ms(0)


def test_estimate_uses_observed_frame_interval():
    interval = RollingStat()
    processing = RollingStat()
    dispatch = RollingStat()
    for _ in range(4):
        interval.add(40)
        processing.add(2)
        dispatch.add(0.5)

    assert estimate_pipeline(30, interval, processing, dispatch) == pytest.approx(42.5)


def test_estimate_uses_requested_fps_until_frame_interval_is_measured():
    processing = RollingStat()
    processing.add(2)

    assert estimate_pipeline(30, RollingStat(), processing, RollingStat()) == pytest.approx(
        1000 / 30 + 2
    )


def test_estimate_is_unavailable_until_processing_is_measured():
    assert estimate_pipeline(30, RollingStat(), RollingStat(), RollingStat()) is None


def test_rolling_stat_discards_values_outside_its_window():
    stat = RollingStat(maxlen=3)
    for value in (1, 1, 1, 100):
        stat.add(value)

    assert len(stat) == 3
    assert stat.mean == pytest.approx(34)
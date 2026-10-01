import pytest

from nottrigger.ui.widgets import normalize_numeric_entry


@pytest.mark.parametrize(
    ("text", "lower", "upper", "resolution", "expected"),
    [
        ("37", 5, 100, 1, 37),
        ("-10", 0, 255, 1, 0),
        ("400", 1, 300, 1, 300),
        ("37.56", 5, 100, 1, 38),
        ("37.56", 5, 100, 0.1, 37.6),
    ],
)
def test_numeric_entry_is_clamped_and_quantized(text, lower, upper, resolution, expected):
    assert normalize_numeric_entry(text, lower, upper, resolution) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "abc", "nan", "inf"])
def test_invalid_numeric_entry_is_rejected(text):
    assert normalize_numeric_entry(text, 0, 100) is None


def test_numeric_entry_supports_tenth_percent_values():
    assert normalize_numeric_entry("35.6", 5, 100, 0.1) == pytest.approx(35.6)


def test_non_positive_step_is_rejected():
    assert normalize_numeric_entry("35", 5, 100, 0) is None
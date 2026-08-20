import math

from market_memory import analysis


def classify(avg_return):
    if math.isnan(avg_return):
        return "NEUTRAL"
    if avg_return > 0:
        return "LONG"
    if avg_return < 0:
        return "SHORT"
    return "NEUTRAL"


def test_direction_zero_is_neutral():
    assert classify(0.0) == "NEUTRAL"


def test_direction_positive_is_long():
    assert classify(1.0) == "LONG"


def test_direction_negative_is_short():
    assert classify(-1.0) == "SHORT"


def test_direction_nan_is_neutral():
    assert classify(float("nan")) == "NEUTRAL"

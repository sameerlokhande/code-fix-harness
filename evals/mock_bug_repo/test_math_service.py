"""Test suite for math calculation service."""

import pytest
from math_service import calculate_mean, divide_numbers


def test_divide_numbers_valid() -> None:
    assert divide_numbers(10, 2) == 5.0
    assert divide_numbers(-9, 3) == -3.0


def test_divide_numbers_zero() -> None:
    with pytest.raises(ValueError, match="Cannot divide by zero"):
        divide_numbers(10, 0)


def test_calculate_mean_valid() -> None:
    assert calculate_mean([1.0, 2.0, 3.0]) == 2.0


def test_calculate_mean_empty() -> None:
    with pytest.raises(ValueError, match="Cannot calculate mean of empty list"):
        calculate_mean([])
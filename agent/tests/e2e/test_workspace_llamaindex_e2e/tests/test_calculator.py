import pytest
from calculator import Calculator


@pytest.fixture
def calc():
    return Calculator()


@pytest.mark.parametrize(
    "a, b, expected",
    [
        (1, 2, 3),          # positive numbers
        (-1, -2, -3),       # negative numbers
        (0, 0, 0),          # zeros
        (5, -3, 2),         # mixed sign
        (-4, 5, 1),         # mixed sign
    ],
)
def test_add(calc, a, b, expected):
    assert calc.add(a, b) == expected


@pytest.mark.parametrize(
    "a, b, expected",
    [
        (5, 3, 2),          # positive numbers
        (-5, -3, -2),       # negative numbers
        (0, 0, 0),          # zeros
        (5, -3, 8),         # mixed sign
        (-4, 5, -9),        # mixed sign
    ],
)
def test_subtract(calc, a, b, expected):
    assert calc.subtract(a, b) == expected

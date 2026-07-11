import pytest

from src.calculator.calculator import Calculator


@pytest.fixture
def calc():
    """Provide a fresh Calculator instance for each test."""
    return Calculator()


# Valid addition cases
@pytest.mark.parametrize(
    "a, b, expected",
    [
        (1, 2, 3),
        (0, 5, 5),
        (-3, 4, 1),
        (5, -2, 3),
    ],
)
def test_addition(calc, a, b, expected):
    assert calc.add(a, b) == expected


# Valid subtraction cases
@pytest.mark.parametrize(
    "a, b, expected",
    [
        (5, 3, 2),
        (2, 5, -3),
        (0, 0, 0),
        (-2, 3, -5),
    ],
)
def test_subtraction(calc, a, b, expected):
    assert calc.subtract(a, b) == expected


# Edge cases with zero
def test_add_zero(calc):
    assert calc.add(0, 0) == 0
    assert calc.add(0, 7) == 7
    assert calc.add(-5, 0) == -5


def test_subtract_zero(calc):
    assert calc.subtract(0, 0) == 0
    assert calc.subtract(7, 0) == 7
    assert calc.subtract(0, 7) == -7


# Invalid input handling
@pytest.mark.parametrize(
    "a, b",
    [
        ("a", 1),
        (1, "b"),
        (None, 2),
        ([1, 2], 3),
    ],
)
def test_invalid_inputs_add(calc, a, b):
    with pytest.raises(TypeError):
        calc.add(a, b)


@pytest.mark.parametrize(
    "a, b",
    [
        ("a", 1),
        (1, "b"),
        (None, 2),
        ([1, 2], 3),
    ],
)
def test_invalid_inputs_subtract(calc, a, b):
    with pytest.raises(TypeError):
        calc.subtract(a, b)

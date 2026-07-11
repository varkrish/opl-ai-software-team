"""Calculator package initialization.

Provides the :class:`Calculator` class with basic arithmetic operations.
"""

from __future__ import annotations
from typing import Union

Number = Union[int, float]


class Calculator:
    """Simple calculator supporting addition and subtraction.

    The methods validate that inputs are numeric (int or float) and raise a
    :class:`TypeError` for any other type, satisfying the requirement to handle
    invalid inputs.
    """

    @staticmethod
    def _validate_number(value: object) -> None:
        """Validate that *value* is an int or float.

        Args:
            value: The value to validate.

        Raises:
            TypeError: If *value* is not a numeric type.
        """
        if not isinstance(value, (int, float)):
            raise TypeError(
                f"Expected numeric type for calculation, got {type(value).__name__}"
            )

    def add(self, a: Number, b: Number) -> Number:
        """Return the sum of *a* and *b*.

        Args:
            a: First addend.
            b: Second addend.

        Returns:
            The arithmetic sum of *a* and *b*.
        """
        self._validate_number(a)
        self._validate_number(b)
        return a + b

    def subtract(self, a: Number, b: Number) -> Number:
        """Return the difference of *a* minus *b*.

        Args:
            a: Minuend.
            b: Subtrahend.

        Returns:
            The arithmetic difference of *a* and *b*.
        """
        self._validate_number(a)
        self._validate_number(b)
        return a - b


__all__ = ["Calculator"]
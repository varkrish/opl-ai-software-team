# Calculator module providing a simple Calculator class for basic arithmetic operations.

"""
Calculator module providing a simple Calculator class for basic arithmetic operations.
"""

from __future__ import annotations
from typing import Union

Number = Union[int, float]


class Calculator:
    """
    A simple calculator supporting addition and subtraction.

    Methods
    -------
    add(a, b):
        Returns the sum of a and b.
    subtract(a, b):
        Returns the difference of a and b (a - b).
    """

    @staticmethod
    def _validate_number(value: object) -> Number:
        """Validate that the provided value is an int or float.

        Parameters
        ----------
        value : object
            The value to validate.

        Returns
        -------
        Number
            The validated numeric value.

        Raises
        ------
        TypeError
            If the value is not an int or float.
        """
        if isinstance(value, (int, float)):
            return value
        raise TypeError(f"Expected int or float, got {type(value).__name__}")

    def add(self, a: Number, b: Number) -> Number:
        """Return the sum of two numbers.

        Parameters
        ----------
        a : int or float
        b : int or float

        Returns
        -------
        int or float
            The arithmetic sum of a and b.

        Raises
        ------
        TypeError
            If either a or b is not a number.
        """
        a_val = self._validate_number(a)
        b_val = self._validate_number(b)
        return a_val + b_val

    def subtract(self, a: Number, b: Number) -> Number:
        """Return the difference of two numbers (a - b).

        Parameters
        ----------
        a : int or float
        b : int or float

        Returns
        -------
        int or float
            The arithmetic difference of a and b.

        Raises
        ------
        TypeError
            If either a or b is not a number.
        """
        a_val = self._validate_number(a)
        b_val = self._validate_number(b)
        return a_val - b_val
